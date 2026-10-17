"""ส่งโค้ดให้ morty-reviewer ตรวจทาน

ใช้งาน:  .venv/bin/python -m morty.review path/to/file.py [อีกหลายไฟล์]
         git diff | .venv/bin/python -m morty.review              # อ่าน diff จาก stdin
         .venv/bin/python -m morty.review --diff                   # เท่ากับ git diff ของไฟล์ที่ยังไม่ staged
         .venv/bin/python -m morty.review --diff --staged          # เฉพาะที่ staged แล้ว
         .venv/bin/python -m morty.review app.py --model qwen3:14b --focus "ความปลอดภัย"
         .venv/bin/python -m morty.review app.py --quick                  # รอบเดียว เร็ว ไม่มี analyzer/ตรวจซ้ำ

ค่าเริ่มต้น: โมเดลตรวจรอบเดียว (โหมดที่วัดแล้วจับบั๊กได้ครบที่สุด) แล้วแนบผล ruff + bandit ต่อท้าย
--pipeline เพิ่มรอบตรวจซ้ำทุก finding (ลด false positive แลกเวลา ~2 เท่า)  --thorough ตรวจ 4 รอบแยกหัวข้อ (~5 เท่า)
--quick ไม่รัน analyzer (ดู reviewpipe.py และผลวัดใน docs/reference.md)
ไฟล์จะถูกใส่เลขบรรทัดให้ เพื่อให้ reviewer อ้างอิงบรรทัดได้แม่นยำ
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console

from . import config, llm, log, profiles, reviewpipe

MAX_CHARS = 60_000  # เกินนี้โมเดล 7b เริ่มลืมส่วนต้น ควรแบ่งไฟล์


def numbered(text: str) -> str:
    lines = text.splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{i + 1:>{width}} | {line}" for i, line in enumerate(lines))


def build_prompt(sections: list[tuple[str, str, bool]], focus: str | None) -> str:
    """sections = [(ชื่อ, เนื้อหา, ใส่เลขบรรทัดไหม)]"""
    parts = ["ทบทวนโค้ดต่อไปนี้ตามรูปแบบที่กำหนด"]
    if focus:
        parts.append(f"ให้น้ำหนักเป็นพิเศษกับ: {focus}")
    for name, body, number_lines in sections:
        lang = Path(name).suffix.lstrip(".") or ""
        content = numbered(body) if number_lines else body
        parts.append(f"### {name}\n```{lang}\n{content}\n```")
    return "\n\n".join(parts)


def collect(args) -> list[tuple[str, str, bool]]:
    sections: list[tuple[str, str, bool]] = []
    if args.diff:
        cmd = ["git", "diff"] + (["--staged"] if args.staged else [])
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=config.ROOT, check=True).stdout
        if not out.strip():
            raise SystemExit("git diff ว่างเปล่า ไม่มีอะไรให้ตรวจ")
        sections.append(("git diff" + (" --staged" if args.staged else ""), out, False))
    for p in args.paths:
        path = Path(p)
        if not path.is_file():
            raise SystemExit(f"ไม่พบไฟล์: {p}")
        sections.append((p, path.read_text(encoding="utf-8", errors="replace"), True))
    if not sections and not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            is_diff = data.lstrip().startswith(("diff --git", "--- ", "+++ ", "@@"))
            sections.append(("stdin (diff)" if is_diff else "stdin", data, not is_diff))
    if not sections:
        raise SystemExit("ไม่มีโค้ดให้ตรวจ ระบุไฟล์, ใช้ --diff หรือ pipe เข้ามาทาง stdin")
    return sections


def main() -> None:
    parser = argparse.ArgumentParser(description="ส่งโค้ดให้ morty-reviewer ตรวจทาน")
    parser.add_argument("paths", nargs="*", help="ไฟล์ที่ต้องการให้ตรวจ")
    parser.add_argument("--diff", action="store_true", help="ตรวจ git diff ของ repo นี้")
    parser.add_argument("--staged", action="store_true", help="ใช้คู่กับ --diff เอาเฉพาะที่ staged")
    parser.add_argument("--profile", default="reviewer", choices=profiles.names())
    parser.add_argument("--model", default=None, help="ทับโมเดลของ profile เช่น qwen3:14b")
    parser.add_argument("--focus", default=None, help="ประเด็นที่อยากให้เน้น เช่น ความปลอดภัย")
    parser.add_argument("--quick", action="store_true", help="รอบเดียว ไม่รัน analyzer")
    parser.add_argument("--pipeline", action="store_true", help="เพิ่มรอบตรวจซ้ำทุก finding (ลด false positive)")
    parser.add_argument("--thorough", action="store_true", help="ตรวจ 4 รอบแยกหัวข้อ + ตรวจซ้ำ (ช้าสุด)")
    args = parser.parse_args()

    console = Console(stderr=True)
    prof = profiles.load(args.profile)
    model = args.model or prof.model or config.MODEL
    sections = collect(args)
    prompt = build_prompt(sections, args.focus)
    if len(prompt) > MAX_CHARS:
        console.print(f"[yellow]คำเตือน:[/] โค้ดยาว {len(prompt):,} ตัวอักษร เกิน {MAX_CHARS:,} โมเดลอาจตรวจไม่ทั่ว ควรแบ่งไฟล์")

    names = ", ".join(s[0] for s in sections)
    console.print(f"[bold green]{prof.title}[/] ตรวจ [cyan]{names}[/] ด้วย [magenta]{model}[/]")
    t0 = time.perf_counter()
    py_files = [Path(p) for p in args.paths if p.endswith(".py")]
    if args.diff:
        cmd = ["git", "diff", "--name-only"] + (["--staged"] if args.staged else [])
        changed = subprocess.run(cmd, capture_output=True, text=True, cwd=config.ROOT).stdout.split()
        py_files += [config.ROOT / f for f in changed if f.endswith(".py") and (config.ROOT / f).is_file()]
    snippet = None
    if not py_files and sections and sections[0][0].startswith("stdin") and not sections[0][0].endswith("(diff)"):
        snippet = (sections[0][1], "python")
    meta: dict = {"mode": "quick"}
    if args.quick:
        messages: list[llm.Message] = [
            {"role": "system", "content": prof.system},
            {"role": "user", "content": prompt},
        ]
        parts_out: list[str] = []
        try:
            for piece in llm.chat_stream(messages, model=model, **dict(prof.options)):
                parts_out.append(piece)
                sys.stdout.write(piece)
                sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        sys.stdout.write("\n")
        answer = "".join(parts_out)
    elif not (args.pipeline or args.thorough):
        text, report = reviewpipe.single_review(
            prompt, py_files=py_files or None, snippet=snippet, model=model, profile_name=prof.name,
            on_event=lambda kind, msg: console.print(f"[dim]• {msg}[/]"),
        )
        answer = reviewpipe.render_single(text, report)
        sys.stdout.write(answer + "\n")
        meta = {"mode": "single+analyzer", "analyzer": report.analyzer_ran,
                "analyzer_findings": {s: sum(1 for f in report.findings if f.severity == s) for s in ("bug", "risk", "style")}}
    else:
        report = reviewpipe.review(
            prompt, py_files=py_files or None, snippet=snippet, model=model, profile_name=prof.name,
            on_event=lambda kind, msg: console.print(f"[dim]• {msg}[/]"), thorough=args.thorough,
        )
        answer = report.render()
        sys.stdout.write(answer + "\n")
        meta = {
            "mode": "thorough" if args.thorough else "pipeline", "passes": report.passes, "dropped": report.verified_dropped,
            "analyzer": report.analyzer_ran,
            "findings": {s: sum(1 for f in report.findings if f.severity == s) for s in ("bug", "risk", "style")},
        }
    event_id = log.record(
        source="review", profile=prof.name, model=model, question=prompt, answer=answer,
        seconds=time.perf_counter() - t0, refs=[s[0] for s in sections],
        meta=meta,
    )
    console.print(f"[dim]บันทึก log {event_id}  ถ้าผลไม่ดี: python -m morty.log rate {event_id} bad \"เหตุผล\"[/]")


if __name__ == "__main__":
    main()
