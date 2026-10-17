"""pipeline ตรวจโค้ดของ morty-reviewer: analyzer → ตรวจหลายรอบแยกหัวข้อ → ตรวจซ้ำ finding → รวมผล

1. static analyzer (ruff + bandit) บนไฟล์ Python จับคลาสบั๊กที่เครื่องมือจับได้แน่นอน ไม่ต้องหวังพึ่งโมเดล
2. โมเดลตรวจทีละหัวข้อ (ความถูกต้อง, ความปลอดภัย, ความทนทาน, style) โมเดลเล็กทำงานแคบได้ดีกว่างานกว้าง
3. รอบตรวจซ้ำ: ทุก finding ระดับ bug/risk ต้องยกอินพุตที่ทำให้พังจริงได้ ไม่ได้ก็ถอน ลด false positive
4. รวม ตัดซ้ำ เรียงตามความรุนแรง แสดงในรูปแบบเดียวกับ reviewer ปกติ
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config, llm, profiles

Event = Callable[[str, str], None]
SEVERITY_ORDER = {"bug": 0, "risk": 1, "style": 2}

ASPECTS = [
    ("correctness", "ความถูกต้อง", "ตรวจเฉพาะความถูกต้องของ logic: บั๊ก, เงื่อนไขขอบ (off-by-one, ค่าว่าง, None), การจัดการข้อผิดพลาด, resource ที่ไม่ถูกปิด, สัญญาของฟังก์ชันตามคอมเมนต์และ type hint (เช่น เรียก coroutine โดยไม่ await) ไม่ต้องพูดเรื่องความปลอดภัยหรือ style"),
    ("security", "ความปลอดภัย", "ตรวจเฉพาะความปลอดภัย: injection ทุกชนิด (SQL, shell, path traversal, template), ความลับที่ฝังในโค้ด, input ที่ไม่ได้ตรวจ, สิทธิ์, deserialization ที่อันตราย ไม่ต้องพูดเรื่องอื่น"),
    ("robustness", "ความทนทาน", "ตรวจเฉพาะความทนทาน: การทำงานพร้อมกัน, timeout, retry, การหารด้วยศูนย์, overflow, ค่า default ที่อันตราย (mutable default), การเทียบ float, พฤติกรรมเมื่ออินพุตผิดรูป ไม่ต้องพูดเรื่องอื่น"),
    ("style", "ความชัดเจน", "ตรวจเฉพาะความชัดเจนและการดูแลรักษา รายงานเฉพาะที่กระทบการอ่านหรือแก้ไขจริงในระดับ [style] เท่านั้น ห้ามยกระดับเป็น bug หรือ risk ถ้าไม่มีอะไรสำคัญให้ตอบว่าไม่พบ"),
]

FINDING_RE = re.compile(r"^\s*[-*•]?\s*\[(bug|risk|style)\]\s*(.+)$", re.IGNORECASE)
RUFF_BUG = {"F821", "F822", "F823", "F811", "F601", "F602", "F631", "F632", "F702", "F704", "F706", "F707", "E999", "B006", "B008", "B015", "B018", "B020", "B023", "B904", "F841"}
RUFF_RISK_PREFIX = ("S", "B")  # bandit-rules ใน ruff และ bugbear ที่เหลือ


@dataclass
class Finding:
    severity: str          # bug | risk | style
    text: str              # "บรรทัด 3: ปัญหา เพราะ ... ควร ..."
    source: str            # ruff:F821 | bandit:B307 | model:correctness
    verified: bool = False
    note: str = ""

    def key(self) -> str:
        """คีย์ตัดซ้ำ: บรรทัดเดียวกัน + ระดับเดียวกัน ถือว่าเป็นประเด็นเดียวกัน
        (analyzer ถูกใส่ก่อนจึงชนะ โมเดลมักบรรยายปัญหาเดียวกันด้วยถ้อยคำต่างกัน)"""
        m = re.search(r"(\d+)", self.text)
        line = m.group(1) if m else "?"
        return f"{line}:{self.severity}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    analyzer_ran: bool = False
    passes: int = 0
    verified_dropped: int = 0

    def verdict(self) -> str:
        n = {s: sum(1 for f in self.findings if f.severity == s) for s in SEVERITY_ORDER}
        blocking = n["bug"] + n["risk"]
        head = f"- พบ {n['bug']} bug, {n['risk']} risk, {n['style']} style: " + ("ต้องแก้ก่อน merge" if blocking else "ผ่าน")
        return head

    def render(self) -> str:
        lines = [self.verdict()]
        if not self.findings:
            lines.append("ไม่พบปัญหาร้ายแรง")
        for f in sorted(self.findings, key=lambda f: SEVERITY_ORDER[f.severity]):
            tag = f" ({f.source})" if not f.source.startswith("model") else ""
            lines.append(f"[{f.severity}] {f.text}{tag}")
        meta = []
        if self.analyzer_ran:
            meta.append("analyzer: ruff + bandit")
        meta.append(f"ตรวจ {self.passes} รอบ")
        if self.verified_dropped:
            meta.append(f"ถอน {self.verified_dropped} finding ที่ยืนยันไม่ได้")
        lines.append("")
        lines.append("(" + ", ".join(meta) + ")")
        return "\n".join(lines)


# ---------- 1. analyzer ----------

def _run_json(cmd: list[str]) -> object | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def analyze_python(files: list[Path]) -> list[Finding]:
    """รัน ruff + bandit บนไฟล์ .py คืน finding ที่ยืนยันแล้ว (เครื่องมือจับได้จริง)"""
    files = [f for f in files if f.suffix == ".py" and f.is_file()]
    if not files:
        return []
    venv_bin = Path(sys.executable).parent
    out: list[Finding] = []
    paths = [str(f) for f in files]

    ruff = _run_json([str(venv_bin / "ruff"), "check", "--output-format", "json", "--select", "E9,F,B,S",
                      "--ignore", "S101", "--no-cache", *paths])
    for d in ruff or []:
        code = d.get("code") or ""
        row = d["location"]["row"]
        name = Path(d["filename"]).name if len(files) > 1 else ""
        where = f"{name} บรรทัด {row}" if name else f"บรรทัด {row}"
        sev = "bug" if code in RUFF_BUG else ("risk" if code.startswith(RUFF_RISK_PREFIX) else "style")
        out.append(Finding(sev, f"{where}: {d['message']}", f"ruff:{code}", verified=True))

    bandit = _run_json([str(venv_bin / "bandit"), "-q", "-f", "json", *paths])
    seen = {f.key() for f in out}
    for r in (bandit or {}).get("results", []):
        sev = "bug" if r["issue_severity"] == "HIGH" else "risk"
        name = Path(r["filename"]).name if len(files) > 1 else ""
        where = f"{name} บรรทัด {r['line_number']}" if name else f"บรรทัด {r['line_number']}"
        f = Finding(sev, f"{where}: {r['issue_text']}", f"bandit:{r['test_id']}", verified=True)
        if f.key() not in seen:
            out.append(f)
    return out


def analyze_snippet(code: str, lang: str) -> list[Finding]:
    if lang not in ("python", "py", ""):
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(code)
        path = Path(tmp.name)
    try:
        return analyze_python([path])
    finally:
        path.unlink(missing_ok=True)


# ---------- 2. โมเดลตรวจทีละหัวข้อ ----------

def parse_findings(text: str, source: str) -> list[Finding]:
    out = []
    for line in text.splitlines():
        m = FINDING_RE.match(line)
        if m:
            out.append(Finding(m.group(1).lower(), m.group(2).strip(), source))
    return out


def model_pass(prof: profiles.Profile, model: str, code_prompt: str, aspect: tuple, known: list[Finding]) -> list[Finding]:
    key, title, instruction = aspect
    known_txt = "\n".join(f"- [{f.severity}] {f.text}" for f in known) or "- (ยังไม่มี)"
    system = (
        prof.system
        + f"\n\nรอบนี้: {title}\n{instruction}\n"
        "ตอบเป็นรายการ finding เท่านั้น บรรทัดละข้อ ขึ้นต้นด้วย [bug] [risk] หรือ [style] ตามด้วย บรรทัด: ปัญหา เพราะอะไร ควรแก้อย่างไร\n"
        "ถ้าไม่พบในหัวข้อนี้ให้ตอบคำเดียวว่า ไม่พบ\n"
        "สิ่งที่เครื่องมืออัตโนมัติหรือรอบก่อนพบแล้ว ไม่ต้องรายงานซ้ำ:\n" + known_txt
    )
    text = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": code_prompt}],
                    model=model, **prof.options)
    return parse_findings(text, f"model:{key}")


# ---------- 3. ตรวจซ้ำ ----------

VERIFY_RE = re.compile(r"^\s*(\d+)\s*[:.)-]\s*(KEEP|DROP|DOWNGRADE)\b(.*)$", re.IGNORECASE)


def verify(prof: profiles.Profile, model: str, code_prompt: str, findings: list[Finding]) -> tuple[list[Finding], int]:
    todo = [f for f in findings if not f.verified and f.severity in ("bug", "risk")]
    if not todo:
        return findings, 0
    listing = "\n".join(f"{i + 1}. [{f.severity}] {f.text}" for i, f in enumerate(todo))
    system = (
        "คุณคือผู้ตรวจสอบความเห็นของ code reviewer อีกชั้นหนึ่ง เข้มงวดกับ false positive\n"
        "สำหรับแต่ละ finding ให้ตัดสินจากโค้ด \"ตามที่เห็น\" เท่านั้น:\n"
        "- KEEP ถ้ายกอินพุตหรือสถานการณ์ที่เป็นรูปธรรมซึ่งทำให้โค้ดนี้ทำงานผิดหรือเป็นช่องโหว่ได้จริง ให้ระบุอินพุตนั้น\n"
        "- DOWNGRADE ถ้าเป็นเรื่องจริงแต่ไม่ทำให้พัง เป็นแค่ควรปรับ (จะกลายเป็น style)\n"
        "- DROP ถ้ายกอินพุตที่ทำให้พังไม่ได้ เป็นการสมมติจากโค้ดที่มองไม่เห็น หรือเป็นแบบแผนที่ถูกต้องอยู่แล้ว "
        "(เช่น except เฉพาะชนิด, การไม่ตรวจชนิดไฟล์ที่ผู้เรียกรับผิดชอบ)\n"
        "ตอบบรรทัดละข้อในรูปแบบ  N: KEEP|DROP|DOWNGRADE - เหตุผลสั้นๆ  ห้ามเพิ่ม finding ใหม่"
    )
    user = f"{code_prompt}\n\n### finding ที่ต้องตรวจ\n{listing}"
    text = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                    model=model, **{**prof.options, "temperature": 0.0})
    decisions: dict[int, tuple[str, str]] = {}
    for line in text.splitlines():
        m = VERIFY_RE.match(line)
        if m:
            decisions[int(m.group(1))] = (m.group(2).upper(), m.group(3).strip(" -:"))
    kept: list[Finding] = []
    dropped = 0
    for f in findings:
        if f not in todo:
            kept.append(f)
            continue
        verdict, why = decisions.get(todo.index(f) + 1, ("KEEP", "ไม่ได้รับคำตัดสิน"))
        if verdict == "DROP":
            dropped += 1
            continue
        if verdict == "DOWNGRADE":
            f.severity = "style"
        f.verified = verdict == "KEEP"
        f.note = why
        kept.append(f)
    return kept, dropped


# ---------- 4. รวม ----------

def merge(findings: list[Finding]) -> list[Finding]:
    """ตัดซ้ำตาม (บรรทัด, ระดับ) และถ้าบรรทัดเดียวกันมีหลายระดับ เก็บเฉพาะระดับที่หนักที่สุด"""
    seen: dict[str, Finding] = {}
    for f in findings:
        seen.setdefault(f.key(), f)
    by_line: dict[str, list[Finding]] = {}
    for f in seen.values():
        by_line.setdefault(f.key().split(":")[0], []).append(f)
    out: list[Finding] = []
    for line, group in by_line.items():
        if line == "?":
            out.extend(group)
            continue
        worst = min(SEVERITY_ORDER[g.severity] for g in group)
        out.extend(g for g in group if SEVERITY_ORDER[g.severity] == worst)
    return out


def review(code_prompt: str, *, py_files: list[Path] | None = None, snippet: tuple[str, str] | None = None,
           model: str | None = None, profile_name: str = "reviewer", on_event: Event | None = None,
           aspects: list[tuple] | None = None) -> Report:
    """code_prompt = ข้อความที่ให้โมเดลดู (มีโค้ดอยู่แล้ว); py_files หรือ snippet=(code, lang) สำหรับ analyzer"""
    prof = profiles.load(profile_name)
    model = model or prof.model or config.MODEL
    report = Report()

    def ev(kind: str, msg: str) -> None:
        if on_event:
            on_event(kind, msg)

    if py_files:
        report.findings += analyze_python(py_files)
        report.analyzer_ran = True
    elif snippet:
        report.findings += analyze_snippet(*snippet)
        report.analyzer_ran = True
    if report.analyzer_ran:
        ev("step", f"analyzer พบ {len(report.findings)} รายการ")

    for aspect in aspects or ASPECTS:
        found = model_pass(prof, model, code_prompt, aspect, report.findings)
        report.passes += 1
        ev("step", f"รอบ {aspect[1]}: {len(found)} finding")
        report.findings = merge(report.findings + found)

    report.findings, report.verified_dropped = verify(prof, model, code_prompt, report.findings)
    ev("step", f"ตรวจซ้ำ: ถอน {report.verified_dropped} คงเหลือ {len(report.findings)}")
    return report
