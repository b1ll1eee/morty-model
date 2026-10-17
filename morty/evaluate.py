"""รัน eval set ของ profile แล้วสรุปคะแนน

ใช้งาน:  .venv/bin/python -m morty.evaluate dev
         .venv/bin/python -m morty.evaluate dev --model qwen3:14b --show
         .venv/bin/python -m morty.evaluate dev --models qwen2.5:7b,qwen2.5-coder:7b,qwen3:14b

ไฟล์คำถามอยู่ที่ evals/<profile>.jsonl หนึ่งบรรทัดต่อหนึ่งข้อ รูปแบบ
  {"id": "py-json", "q": "คำถาม", "expect_any": ["คำที่ควรมีอย่างน้อยหนึ่งคำ"],
   "expect_all": ["คำที่ต้องมีทุกคำ"], "thai": true}
การตรวจเป็นแบบหยาบ (หาคำในคำตอบ) ใช้จับการถดถอยเวลาแก้ prompt หรือสลับโมเดล
ไม่ได้แทนการอ่านคำตอบจริง ใส่ --show เพื่อดูคำตอบทั้งหมด
ผลแต่ละครั้งถูกบันทึกไว้ที่ evals/results/<profile>-<rag|norag>-<โมเดล>-<เวลา>.jsonl
--models จะรันทุกโมเดลบน eval เดียวกันแล้วสรุปเป็นตารางเทียบ พร้อมบันทึก evals/results/compare-<เวลา>.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import config, llm, profiles

EVALS_DIR = config.ROOT / "evals"
THAI = re.compile(r"[฀-๿]")


def load_cases(name: str) -> list[dict]:
    path = EVALS_DIR / f"{name}.jsonl"
    if not path.is_file():
        raise SystemExit(f"ไม่พบไฟล์ eval: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(case: dict, answer: str) -> list[str]:
    """คืนรายการเหตุผลที่ตก ถ้าว่างแปลว่าผ่าน"""
    low = answer.lower()
    fails: list[str] = []
    if any_ := case.get("expect_any"):
        if not any(k.lower() in low for k in any_):
            fails.append("ไม่พบ: " + " | ".join(any_))
    for k in case.get("expect_all", []):
        if k.lower() not in low:
            fails.append(f"ขาด: {k}")
    if case.get("thai") and not THAI.search(answer):
        fails.append("ไม่ได้ตอบภาษาไทย")
    return fails


def run(profile_name: str, model: str, cases: list[dict], *, rag: bool, show: bool, console: Console) -> list[dict]:
    """รัน eval หนึ่งรอบด้วยโมเดลเดียว พิมพ์ตาราง และคืน record ต่อข้อ"""
    console.print(
        f"eval [magenta]{profile_name}[/] {len(cases)} ข้อ ด้วยโมเดล [cyan]{model}[/] "
        f"{'RAG เปิด' if rag else 'RAG ปิด'}"
    )
    table = Table(show_lines=False)
    table.add_column("id", style="bold")
    table.add_column("ผล")
    table.add_column("เวลา", justify="right")
    table.add_column("หมายเหตุ")

    records: list[dict] = []
    for case in cases:
        t0 = time.perf_counter()
        answer = llm.ask(case["q"], profile=profile_name, model=model, rag=rag)
        dt = time.perf_counter() - t0
        fails = check(case, answer)
        ok = not fails
        table.add_row(
            case["id"], "[green]ผ่าน[/]" if ok else "[red]ตก[/]", f"{dt:.1f}s", "; ".join(fails)
        )
        records.append({**case, "model": model, "rag": rag, "answer": answer, "pass": ok, "fails": fails, "seconds": round(dt, 2)})
        if show:
            console.rule(f"{case['id']}  {'ผ่าน' if ok else 'ตก'}")
            console.print(f"[blue]ถาม:[/] {case['q']}")
            console.print(answer, markup=False, highlight=False)

    passed = sum(r["pass"] for r in records)
    total_time = sum(r["seconds"] for r in records)
    console.print(table)
    console.print(
        f"ผ่าน [bold]{passed}/{len(cases)}[/] ({passed / len(cases):.0%})  "
        f"เฉลี่ย {total_time / len(cases):.1f}s ต่อข้อ"
    )
    return records


def summary(records: list[dict]) -> dict:
    n = len(records)
    passed = sum(r["pass"] for r in records)
    secs = [r["seconds"] for r in records]
    return {
        "model": records[0]["model"], "rag": records[0]["rag"], "cases": n, "passed": passed,
        "score": round(passed / n, 3), "avg_seconds": round(sum(secs) / n, 1), "max_seconds": max(secs),
        "failed_ids": [r["id"] for r in records if not r["pass"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="รัน eval set ของ profile")
    parser.add_argument("profile", choices=profiles.names())
    parser.add_argument("--model", default=None, help="ทับโมเดลของ profile")
    parser.add_argument("--models", default=None, help="หลายโมเดลคั่นด้วยจุลภาค รันทุกตัวแล้วสรุปตารางเทียบ")
    parser.add_argument("--show", action="store_true", help="แสดงคำตอบเต็มทุกข้อ")
    parser.add_argument("--no-rag", action="store_true", help="ปิด RAG เพื่อเทียบผลก่อนและหลัง")
    args = parser.parse_args()

    console = Console()
    prof = profiles.load(args.profile)
    cases = load_cases(args.profile)
    rag = not args.no_rag
    models = [m.strip() for m in args.models.split(",")] if args.models else [args.model or prof.model or config.MODEL]

    out_dir = EVALS_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = "rag" if rag else "norag"

    summaries: list[dict] = []
    for model in models:
        records = run(args.profile, model, cases, rag=rag, show=args.show, console=console)
        safe = model.replace(":", "_").replace("/", "_")
        out = out_dir / f"{args.profile}-{tag}-{safe}-{stamp}.jsonl"
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
        console.print(f"[dim]บันทึกผลที่ {out.relative_to(config.ROOT)}[/]\n")
        summaries.append(summary(records))

    if len(models) > 1:
        table = Table(title=f"เทียบโมเดลบน eval {args.profile} ({tag})")
        table.add_column("โมเดล", style="cyan")
        table.add_column("ผ่าน", justify="right")
        table.add_column("เฉลี่ย/ข้อ", justify="right")
        table.add_column("ช้าสุด", justify="right")
        table.add_column("ข้อที่ตก")
        for sm in summaries:
            table.add_row(
                sm["model"], f"{sm['passed']}/{sm['cases']}", f"{sm['avg_seconds']}s",
                f"{sm['max_seconds']}s", ", ".join(sm["failed_ids"]) or "-",
            )
        console.print(table)
        out = out_dir / f"compare-{args.profile}-{tag}-{stamp}.json"
        out.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]บันทึกสรุปที่ {out.relative_to(config.ROOT)}[/]")


if __name__ == "__main__":
    main()
