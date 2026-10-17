"""เครื่องมือที่โมเดลเรียกใช้ได้ (tool calling) ทำงานภายใน workspace ที่กำหนดเท่านั้น

เครื่องมืออ่านอย่างเดียว (read_file, list_dir, search_code, git) ทำงานทันที
เครื่องมือที่รันคำสั่ง (run_command, run_python) ต้องผ่าน confirm callback ก่อนทุกครั้ง
ทุกผลลัพธ์ถูกตัดความยาวเพื่อไม่ให้ context ของโมเดลล้น
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
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
        p = (self.root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        if p != self.root and self.root not in p.parents:
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
