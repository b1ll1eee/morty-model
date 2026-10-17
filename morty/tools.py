"""เครื่องมือที่โมเดลเรียกใช้ได้ (tool calling) ทำงานภายใน workspace ที่กำหนดเท่านั้น

เครื่องมืออ่านอย่างเดียว (read_file, list_dir, search_code, git) ทำงานทันที
เครื่องมือที่รันคำสั่ง (run_command, run_python) และที่แก้ไฟล์ (edit_file, write_file, apply_patch)
ต้องผ่าน confirm callback ก่อนทุกครั้ง โดยแสดง diff ของสิ่งที่จะเปลี่ยนให้ผู้ใช้เห็นก่อน
ทุกผลลัพธ์ถูกตัดความยาวเพื่อไม่ให้ context ของโมเดลล้น
"""
from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTPUT = 12_000  # ตัวอักษร
MAX_FILE_LINES = 400
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build", "models"}
READ_ONLY_GIT = {"status", "diff", "log", "show", "blame", "branch", "ls-files", "rev-parse"}


@dataclass
class Workspace:
    root: Path
    confirm: Callable[[str], bool] = lambda desc: False  # ถามผู้ใช้ก่อนรันคำสั่ง ค่าเริ่มต้นคือปฏิเสธ
    allow: set[str] = field(default_factory=lambda: set(ALL_TOOLS))

    def resolve(self, path: str) -> Path:
        root = self.root.resolve()  # กัน symlink เช่น /var → /private/var
        p = (root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        if p != root and root not in p.parents:
            raise PermissionError(f"อยู่นอก workspace: {path}")
        return p


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (ตัดออก {len(text) - limit:,} ตัวอักษร)"


# ---------- เครื่องมือ ----------

def read_file(ws: Workspace, path: str, start_line: int = 1, end_line: int | None = None) -> str:
    p = ws.resolve(path)
    if not p.is_file():
        return f"ไม่พบไฟล์: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start_line))
    end = min(len(lines), int(end_line) if end_line else start + MAX_FILE_LINES - 1)
    body = "\n".join(f"{i} | {lines[i - 1]}" for i in range(start, end + 1))
    note = "" if end >= len(lines) else f"\n... ไฟล์มี {len(lines)} บรรทัด แสดงถึงบรรทัด {end} ใช้ start_line เพื่ออ่านต่อ"
    return _clip(body + note)


def list_dir(ws: Workspace, path: str = ".") -> str:
    p = ws.resolve(path)
    if not p.is_dir():
        return f"ไม่พบโฟลเดอร์: {path}"
    rows = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if child.name in SKIP_DIRS or child.name.startswith(".") and child.name not in (".env.example", ".gitignore"):
            continue
        rows.append(f"{child.name}/" if child.is_dir() else f"{child.name}  ({child.stat().st_size:,} B)")
    return _clip("\n".join(rows) or "(ว่าง)")


def search_code(ws: Workspace, pattern: str, path: str = ".", glob: str = "*") -> str:
    base = ws.resolve(path)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"regex ไม่ถูกต้อง: {e}"
    hits: list[str] = []
    for f in sorted(base.rglob(glob)):
        if not f.is_file() or any(part in SKIP_DIRS for part in f.relative_to(ws.root).parts):
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(ws.root)}:{i}: {line.strip()[:160]}")
                    if len(hits) >= 80:
                        hits.append("... (หยุดที่ 80 รายการ ระบุ path หรือ glob ให้แคบลง)")
                        return _clip("\n".join(hits))
        except OSError:
            continue
    return _clip("\n".join(hits) or "ไม่พบ")


def git(ws: Workspace, args: str) -> str:
    parts = args.split()
    if not parts or parts[0] not in READ_ONLY_GIT:
        return f"อนุญาตเฉพาะคำสั่งอ่านอย่างเดียว: {', '.join(sorted(READ_ONLY_GIT))}"
    return _run(["git", *parts], ws.root, timeout=20)


def run_command(ws: Workspace, command: str) -> str:
    if not ws.confirm(f"รันคำสั่ง: {command}"):
        return "ผู้ใช้ไม่อนุญาตให้รันคำสั่งนี้ ให้ตอบจากข้อมูลที่มี หรืออธิบายว่าต้องรันอะไรและทำไม"
    return _run(["/bin/zsh", "-lc", command], ws.root, timeout=60)


def run_python(ws: Workspace, code: str) -> str:
    preview = code if len(code) < 300 else code[:300] + " ..."
    if not ws.confirm(f"รันโค้ด Python:\n{preview}"):
        return "ผู้ใช้ไม่อนุญาตให้รันโค้ดนี้ ให้อธิบายผลที่คาดว่าจะได้แทน"
    return _run([sys.executable, "-c", code], ws.root, timeout=60)


# ---------- แก้ไฟล์ ----------

def _diff(before: str, after: str, name: str) -> str:
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                 fromfile=f"a/{name}", tofile=f"b/{name}")
    text = "".join(lines)
    return text if len(text) <= 4000 else text[:4000] + "\n... (diff ยาว ตัดออก)"


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def edit_file(ws: Workspace, path: str, old: str, new: str) -> str:
    """แทนที่ข้อความ old ด้วย new ในไฟล์ old ต้องพบเพียงครั้งเดียว (วิธีที่โมเดลเล็กทำสำเร็จบ่อยที่สุด)"""
    p = ws.resolve(path)
    if not p.is_file():
        return f"ไม่พบไฟล์: {path} (ถ้าจะสร้างไฟล์ใหม่ใช้ write_file)"
    before = p.read_text(encoding="utf-8")
    n = before.count(old)
    if n == 0:
        return "ไม่พบข้อความ old ในไฟล์ ให้ read_file แล้วคัดลอกข้อความเดิมมาให้ตรงทุกตัวอักษร รวมการเว้นวรรคและขึ้นบรรทัด"
    if n > 1:
        return f"พบข้อความ old {n} แห่ง ต้องระบุให้ยาวขึ้นจนไม่ซ้ำกัน"
    after = before.replace(old, new, 1)
    if not ws.confirm(f"แก้ไฟล์ {path}:\n{_diff(before, after, path)}"):
        return "ผู้ใช้ไม่อนุญาตให้แก้ไฟล์นี้ ให้อธิบายการแก้ที่เสนอแทน"
    _write(p, after)
    return f"แก้ {path} แล้ว ({len(before.splitlines())} → {len(after.splitlines())} บรรทัด)"


def write_file(ws: Workspace, path: str, content: str, overwrite: bool = False) -> str:
    """สร้างไฟล์ใหม่ หรือเขียนทับทั้งไฟล์เมื่อระบุ overwrite=true เท่านั้น
    (eval พบว่าโมเดลชอบเขียนทับไฟล์เดิมแล้วทำฟังก์ชันอื่นหาย จึงบังคับให้ระบุเจตนา)"""
    p = ws.resolve(path)
    before = p.read_text(encoding="utf-8") if p.is_file() else ""
    if before == content:
        return f"{path} มีเนื้อหานี้อยู่แล้ว ไม่ได้เปลี่ยนอะไร"
    if p.is_file() and not overwrite:
        return (f"{path} มีอยู่แล้ว ({len(before.splitlines())} บรรทัด) ไม่เขียนทับ: ถ้าแก้บางส่วนให้ใช้ edit_file "
                "ถ้าตั้งใจแทนที่ทั้งไฟล์จริงๆ ให้เรียก write_file ซ้ำพร้อม overwrite=true และต้องมีเนื้อหาเดิมที่ต้องเก็บครบ")
    what = "เขียนทับ" if p.is_file() else "สร้างไฟล์ใหม่"
    if not ws.confirm(f"{what} {path}:\n{_diff(before, content, path)}"):
        return "ผู้ใช้ไม่อนุญาตให้เขียนไฟล์นี้ ให้แสดงเนื้อหาที่เสนอแทน"
    _write(p, content)
    return f"{what} {path} แล้ว ({len(content.splitlines())} บรรทัด)"


def apply_patch(ws: Workspace, patch: str) -> str:
    """ใช้ unified diff กับไฟล์ใน workspace (ผ่าน git apply --recount จึงทนเลขบรรทัดใน hunk ที่คลาดเคลื่อน)"""
    if "+++ " not in patch or "@@" not in patch:
        return "patch ต้องเป็น unified diff (มีบรรทัด --- a/..., +++ b/... และ @@) ถ้าแก้จุดเล็กๆ ใช้ edit_file ง่ายกว่า"
    if not patch.endswith("\n"):
        patch += "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as tmp:
        tmp.write(patch)
        tmp_path = tmp.name
    # รันใน cwd = workspace โดยตรง โมเดลมักให้ context ไม่ครบและนับบรรทัดใน @@ ผิด
    # git apply ปกติจะปฏิเสธ hunk ที่ context ท้ายไม่ครบถ้าไม่ได้อยู่ท้ายไฟล์ จึงลองชุดตัวเลือกจากเข้มไปผ่อน
    # ทุกชุดยังตรวจว่าบรรทัด context/ลบ ตรงกับไฟล์จริง patch ที่อ้างบรรทัดไม่มีอยู่จะไม่ผ่านอยู่ดี
    attempts = [[], ["--unidiff-zero"], ["--recount"], ["--recount", "--unidiff-zero"], ["-C0"]]
    try:
        for target in re.findall(r"^\+\+\+ b/(.+)$", patch, re.M):
            ws.resolve(target)  # กัน patch ที่ชี้ออกนอก workspace
        chosen: list[str] | None = None
        last_err = ""
        for opts in attempts:
            check = subprocess.run(["git", "apply", *opts, "--whitespace=nowarn", "--check", tmp_path],
                                   capture_output=True, text=True, cwd=ws.root, timeout=20)
            if check.returncode == 0:
                chosen = opts
                break
            last_err = check.stderr.strip()
        if chosen is None:
            return f"patch ใช้ไม่ได้: {last_err[:500]}\nให้ read_file ดูเนื้อหาปัจจุบันแล้วใช้ edit_file แทน"
        if not ws.confirm(f"ใช้ patch:\n{patch if len(patch) <= 4000 else patch[:4000] + '...'}"):
            return "ผู้ใช้ไม่อนุญาตให้ใช้ patch นี้"
        r = subprocess.run(["git", "apply", *chosen, "--whitespace=nowarn", tmp_path],
                           capture_output=True, text=True, cwd=ws.root, timeout=20)
        return "ใช้ patch แล้ว" if r.returncode == 0 else f"ใช้ patch ไม่สำเร็จ: {r.stderr.strip()[:500]}"
    except PermissionError as e:
        return str(e)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _run(cmd: list[str], cwd: Path, timeout: int) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"หมดเวลา {timeout} วินาที"
    out = r.stdout
    if r.stderr:
        out += ("\n[stderr]\n" if out else "[stderr]\n") + r.stderr
    out = out.rstrip() or "(ไม่มีผลลัพธ์)"
    return _clip(f"exit code {r.returncode}\n{out}")


ALL_TOOLS: dict[str, Callable] = {
    "read_file": read_file, "list_dir": list_dir, "search_code": search_code,
    "git": git, "run_command": run_command, "run_python": run_python,
    "edit_file": edit_file, "write_file": write_file, "apply_patch": apply_patch,
}

SCHEMAS = {
    "read_file": {"type": "function", "function": {
        "name": "read_file", "description": "อ่านเนื้อหาไฟล์ใน workspace พร้อมเลขบรรทัด ใช้เมื่อต้องดูโค้ดหรือเอกสารจริง",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "path เทียบกับ workspace เช่น morty/llm.py"},
            "start_line": {"type": "integer", "description": "บรรทัดเริ่ม (ค่าเริ่มต้น 1)"},
            "end_line": {"type": "integer", "description": "บรรทัดสุดท้าย"}},
            "required": ["path"]}}},
    "list_dir": {"type": "function", "function": {
        "name": "list_dir", "description": "แสดงไฟล์และโฟลเดอร์ใน path ที่กำหนด ใช้สำรวจโครงสร้างโปรเจกต์",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "โฟลเดอร์ เช่น . หรือ morty"}}, "required": []}}},
    "search_code": {"type": "function", "function": {
        "name": "search_code", "description": "ค้นหาข้อความหรือ regex ในไฟล์ทั้งโปรเจกต์ ได้ผลเป็น ไฟล์:บรรทัด: ข้อความ ใช้หาว่าฟังก์ชันหรือคำนี้อยู่ที่ไหน",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "regex หรือคำที่ค้น"},
            "path": {"type": "string", "description": "จำกัดโฟลเดอร์ (ค่าเริ่มต้น .)"},
            "glob": {"type": "string", "description": "รูปแบบชื่อไฟล์ เช่น *.py"}},
            "required": ["pattern"]}}},
    "git": {"type": "function", "function": {
        "name": "git", "description": "รัน git แบบอ่านอย่างเดียว: status, diff, log, show, blame, branch, ls-files",
        "parameters": {"type": "object", "properties": {
            "args": {"type": "string", "description": "อาร์กิวเมนต์หลัง git เช่น 'log --oneline -5' หรือ 'diff --stat'"}},
            "required": ["args"]}}},
    "run_command": {"type": "function", "function": {
        "name": "run_command", "description": "รันคำสั่ง shell ใน workspace (ผู้ใช้ต้องอนุญาตก่อน) ใช้รันเทสต์ ดูเวอร์ชัน หรือตรวจสอบผลจริง",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "คำสั่ง เช่น .venv/bin/python -m pytest -q"}},
            "required": ["command"]}}},
    "run_python": {"type": "function", "function": {
        "name": "run_python", "description": "รันโค้ด Python สั้นๆ เพื่อทดลองหรือคำนวณ (ผู้ใช้ต้องอนุญาตก่อน) ผลลัพธ์คือสิ่งที่ print ออกมา",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "โค้ด Python ที่ print ผลลัพธ์"}},
            "required": ["code"]}}},
    "edit_file": {"type": "function", "function": {
        "name": "edit_file", "description": "แก้ไฟล์ที่มีอยู่โดยแทนที่ข้อความ old (ต้องตรงทุกตัวอักษรและพบครั้งเดียว) ด้วย new ผู้ใช้จะเห็น diff และอนุญาตก่อน ใช้วิธีนี้สำหรับการแก้ส่วนใหญ่",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "ข้อความเดิมที่คัดลอกมาจาก read_file แบบไม่มีเลขบรรทัด"},
            "new": {"type": "string", "description": "ข้อความใหม่ที่จะแทนที่"}},
            "required": ["path", "old", "new"]}}},
    "write_file": {"type": "function", "function": {
        "name": "write_file", "description": "สร้างไฟล์ใหม่ (ผู้ใช้เห็น diff และอนุญาตก่อน) ไฟล์ที่มีอยู่แล้วจะไม่ถูกเขียนทับเว้นแต่ส่ง overwrite=true สำหรับการแก้บางส่วนให้ใช้ edit_file",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string", "description": "เนื้อหาทั้งไฟล์"},
            "overwrite": {"type": "boolean", "description": "true เมื่อตั้งใจแทนที่ไฟล์เดิมทั้งไฟล์"}},
            "required": ["path", "content"]}}},
    "apply_patch": {"type": "function", "function": {
        "name": "apply_patch", "description": "ใช้ unified diff (--- a/path, +++ b/path, @@) กับไฟล์ใน workspace ผู้ใช้อนุญาตก่อน ใช้เมื่อแก้หลายจุดในหลายไฟล์พร้อมกัน ถ้าไม่แน่ใจรูปแบบ ให้ใช้ edit_file",
        "parameters": {"type": "object", "properties": {
            "patch": {"type": "string", "description": "ข้อความ unified diff ทั้งก้อน"}},
            "required": ["patch"]}}},
}


def schemas_for(names: list[str] | None = None) -> list[dict]:
    return [SCHEMAS[n] for n in (names or list(SCHEMAS)) if n in SCHEMAS]


def call(ws: Workspace, name: str, args: dict) -> str:
    fn = ALL_TOOLS.get(name)
    if fn is None or name not in ws.allow:
        return f"ไม่มีเครื่องมือชื่อ {name}"
    try:
        return fn(ws, **args)
    except TypeError as e:
        return f"อาร์กิวเมนต์ไม่ถูกต้อง: {e}"
    except PermissionError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"เครื่องมือล้มเหลว: {type(e).__name__}: {e}"
