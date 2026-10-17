"""รัน eval set ของ profile แล้วสรุปคะแนน

ใช้งาน:  .venv/bin/python -m morty.evaluate dev
         .venv/bin/python -m morty.evaluate dev --model qwen3:14b --show
         .venv/bin/python -m morty.evaluate dev --models qwen2.5:7b,qwen2.5-coder:7b,qwen3:14b
         .venv/bin/python -m morty.evaluate dev --set dev-edit --tools --allow-run --workspace-template evals/fixtures/edit

ไฟล์คำถามอยู่ที่ evals/<profile>.jsonl หนึ่งบรรทัดต่อหนึ่งข้อ รูปแบบ
  {"id": "py-json", "q": "คำถาม", "expect_any": ["คำที่ควรมีอย่างน้อยหนึ่งคำ"],
   "expect_all": ["คำที่ต้องมีทุกคำ"], "thai": true}
การตรวจเป็นแบบหยาบ (หาคำในคำตอบ) ใช้จับการถดถอยเวลาแก้ prompt หรือสลับโมเดล
ไม่ได้แทนการอ่านคำตอบจริง ใส่ --show เพื่อดูคำตอบทั้งหมด
--judge ให้โมเดลกรรมการ (ค่าเริ่มต้น qwen3:14b) ให้คะแนน 0-2 ตาม rubric แยกจากการหาคำ
--repeat N รันแต่ละข้อ N รอบ เพื่อดูว่าผลนิ่งแค่ไหน (ข้อที่ผ่านบ้างตกบ้าง = flaky)
ผลแต่ละครั้งถูกบันทึกไว้ที่ evals/results/<profile>-<rag|norag>-<โมเดล>-<เวลา>.jsonl
--models จะรันทุกโมเดลบน eval เดียวกันแล้วสรุปเป็นตารางเทียบ พร้อมบันทึก evals/results/compare-<เวลา>.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import agent, config, dashboard, llm, profiles, reviewpipe

EVALS_DIR = config.ROOT / "evals"
THAI = re.compile(r"[฀-๿]")


def load_cases(name: str) -> list[dict]:
    path = EVALS_DIR / f"{name}.jsonl"
    if not path.is_file():
        raise SystemExit(f"ไม่พบไฟล์ eval: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_files(case: dict, workspace: Path | None) -> list[str]:
    """ตรวจสภาพไฟล์ใน workspace หลังโมเดลทำงาน ตาม expect_file ของข้อนั้น"""
    spec = case.get("expect_file")
    if not spec:
        return []
    if workspace is None:
        return ["ข้อนี้ต้องรันด้วย --tools และ --workspace-template"]
    p = workspace / spec["path"]
    if not p.is_file():
        return [f"ไม่มีไฟล์ {spec['path']}"]
    text = p.read_text(encoding="utf-8", errors="replace")
    fails = [f"ไฟล์ขาด: {k}" for k in spec.get("contains", []) if k not in text]
    fails += [f"ไฟล์ไม่ควรมี: {k}" for k in spec.get("not_contains", []) if k in text]
    return fails


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


def ask_with_tools(profile_name: str, model: str, question: str, allow_run: bool,
                   workspace: Path | None = None) -> str:
    prof = profiles.load(profile_name)
    ws = agent.make_workspace(workspace or Path(config.ROOT), prof, confirm=lambda desc: allow_run)
    messages = [{"role": "system", "content": prof.system}, {"role": "user", "content": question}]
    answer, _ = agent.run(messages, model=model, options={**prof.options}, ws=ws)
    return answer


FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.S)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 2}, "reason": {"type": "string"}},
    "required": ["score", "reason"],
}
JUDGE_SYSTEM = (
    "คุณคือกรรมการตรวจคำตอบของผู้ช่วย AI ให้คะแนนอย่างเข้มงวดและเป็นกลาง\n"
    "2 = ถูกต้องครบถ้วน ตรงคำถาม ไม่มีข้อมูลผิด\n"
    "1 = ถูกบางส่วน หรือถูกแต่ขาดสาระสำคัญ หรือมีส่วนที่ทำให้เข้าใจผิดเล็กน้อย\n"
    "0 = ผิด ไม่ตอบคำถาม หรือแต่งข้อมูล\n"
    "คำใบ้ที่ให้มาคือสิ่งที่คำตอบที่ดีมักมี ไม่ใช่เกณฑ์ตายตัว ใช้ความรู้ของคุณตัดสินเนื้อหาด้วย\n"
    "ตอบเป็น JSON {\"score\": 0|1|2, \"reason\": เหตุผลสั้นๆ ภาษาไทย}"
)


def judge(question: str, answer: str, case: dict, judge_model: str) -> tuple[int | None, str]:
    hints = []
    if case.get("expect_any"):
        hints.append("ควรมีอย่างน้อยหนึ่งใน: " + ", ".join(case["expect_any"]))
    if case.get("expect_all"):
        hints.append("ต้องมี: " + ", ".join(case["expect_all"]))
    if case.get("note"):
        hints.append("หมายเหตุ: " + case["note"])
    user = f"### คำถาม\n{question}\n\n### คำตอบที่ต้องตรวจ\n{answer}\n\n### คำใบ้\n" + ("\n".join(hints) or "-")
    try:
        text = llm.chat([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
                        model=judge_model, temperature=0.0, think=False, format=JUDGE_SCHEMA)
        data = json.loads(text)
        return int(data["score"]), str(data.get("reason", ""))
    except Exception as e:  # noqa: BLE001
        return None, f"judge ล้มเหลว: {e}"


def ask_with_pipeline(profile_name: str, model: str, question: str, thorough: bool = False) -> str:
    m = FENCE_RE.search(question)
    snippet = (m.group(2), m.group(1).lower() or "python") if m else None
    report = reviewpipe.review(question, snippet=snippet, model=model, profile_name=profile_name, thorough=thorough)
    return report.render()


def run(profile_name: str, model: str, cases: list[dict], *, rag: bool, show: bool, console: Console,
        use_tools: bool = False, allow_run: bool = False, use_pipeline: bool = False, thorough: bool = False,
        judge_model: str | None = None, repeat: int = 1, workspace_template: Path | None = None) -> list[dict]:
    """รัน eval ด้วยโมเดลเดียว (แต่ละข้อ repeat รอบ) พิมพ์ตาราง และคืน record ต่อข้อต่อรอบ"""
    console.print(
        f"eval [magenta]{profile_name}[/] {len(cases)} ข้อ ด้วยโมเดล [cyan]{model}[/] "
        f"{'RAG เปิด' if rag else 'RAG ปิด'}" + (f" ×{repeat} รอบ" if repeat > 1 else "")
        + (f" กรรมการ [cyan]{judge_model}[/]" if judge_model else "")
    )
    table = Table(show_lines=False)
    table.add_column("id", style="bold")
    table.add_column("ผล")
    table.add_column("เวลา", justify="right")
    if judge_model:
        table.add_column("กรรมการ", justify="right")
    table.add_column("หมายเหตุ")

    records: list[dict] = []
    digest = llm.model_digest(model)
    for case in cases:
        for run_idx in range(repeat):
            t0 = time.perf_counter()
            work: Path | None = None
            tmp: str | None = None
            if workspace_template is not None:
                # สำเนาใหม่ทุกข้อ โมเดลจึงแก้สำเนา ไม่ใช่ต้นฉบับ และแต่ละข้อไม่เห็นผลของข้อก่อน
                tmp = tempfile.mkdtemp(prefix="morty-eval-")
                work = Path(tmp) / "work"
                shutil.copytree(workspace_template, work)
            if use_pipeline:
                answer = ask_with_pipeline(profile_name, model, case["q"], thorough)
            elif use_tools:
                answer = ask_with_tools(profile_name, model, case["q"], allow_run, workspace=work)
            else:
                answer = llm.ask(case["q"], profile=profile_name, model=model, rag=rag)
            dt = time.perf_counter() - t0
            fails = check(case, answer) + check_files(case, work)
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            ok = not fails
            records.append({**case, "model": model, "model_digest": digest, "rag": rag, "answer": answer,
                            "pass": ok, "fails": fails, "seconds": round(dt, 2), "run": run_idx + 1,
                            "judge_model": judge_model, "judge_score": None, "judge_reason": ""})
            if show:
                console.rule(f"{case['id']}  {'ผ่าน' if ok else 'ตก'}")
                console.print(f"[blue]ถาม:[/] {case['q']}")
                console.print(answer, markup=False, highlight=False)

    # ให้กรรมการตรวจทีเดียวหลังตอบครบทุกข้อ เพื่อไม่ต้องสลับโมเดลตอบกับโมเดลกรรมการไปมาทุกข้อ
    # (สลับทุกข้อทำให้ Ollama โหลด weight ใหม่บ่อย 20 ข้อกินเวลาเกือบชั่วโมง)
    if judge_model:
        console.print(f"[dim]กรรมการ {judge_model} กำลังให้คะแนน {len(records)} คำตอบ...[/]")
        for r in records:
            r["judge_score"], r["judge_reason"] = judge(r["q"], r["answer"], r, judge_model)
    for r in records:
        label = r["id"] + (f" #{r['run']}" if repeat > 1 else "")
        row = [label, "[green]ผ่าน[/]" if r["pass"] else "[red]ตก[/]", f"{r['seconds']:.1f}s"]
        if judge_model:
            row.append("-" if r["judge_score"] is None else str(r["judge_score"]))
        note = "; ".join(r["fails"])
        if judge_model and r["judge_score"] is not None and r["judge_score"] < 2:
            note = (note + " | " if note else "") + f"กรรมการ: {r['judge_reason'][:80]}"
        table.add_row(*row, note)

    passed = sum(r["pass"] for r in records)
    total_time = sum(r["seconds"] for r in records)
    console.print(table)
    line = f"ผ่าน [bold]{passed}/{len(records)}[/] ({passed / len(records):.0%})  เฉลี่ย {total_time / len(records):.1f}s ต่อข้อ"
    scores = [r["judge_score"] for r in records if r.get("judge_score") is not None]
    if scores:
        line += f"  กรรมการเฉลี่ย [bold]{sum(scores) / len(scores):.2f}[/]/2 (ได้ 2 เต็ม {sum(1 for s in scores if s == 2)}/{len(scores)})"
    console.print(line)
    if repeat > 1:
        by_case: dict[str, list[bool]] = {}
        for r in records:
            by_case.setdefault(r["id"], []).append(r["pass"])
        flaky = [f"{k} ({sum(v)}/{len(v)})" for k, v in by_case.items() if 0 < sum(v) < len(v)]
        rates = [sum(v) / len(v) for v in by_case.values()]
        console.print(
            f"ผลนิ่ง: ผ่านทุกรอบ {sum(1 for v in by_case.values() if all(v))}/{len(by_case)} ข้อ  "
            f"ตกทุกรอบ {sum(1 for v in by_case.values() if not any(v))}  "
            f"แกว่ง {len(flaky)}" + (": " + ", ".join(flaky) if flaky else "")
        )
        console.print(f"อัตราผ่านเฉลี่ยต่อข้อ {sum(rates) / len(rates):.0%} (ต่ำสุด {min(rates):.0%} สูงสุด {max(rates):.0%})")
    return records


def summary(records: list[dict]) -> dict:
    n = len(records)
    passed = sum(r["pass"] for r in records)
    secs = [r["seconds"] for r in records]
    scores = [r["judge_score"] for r in records if r.get("judge_score") is not None]
    return {
        "model": records[0]["model"], "model_digest": records[0].get("model_digest"),
        "judge_avg": round(sum(scores) / len(scores), 3) if scores else None,
        "rag": records[0]["rag"], "cases": n, "passed": passed,
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
    parser.add_argument("--set", default=None, help="ชื่อชุด eval ใน evals/ (ค่าเริ่มต้น = ชื่อ profile) เช่น dev-tools")
    parser.add_argument("--tools", action="store_true", help="รันผ่านวงจรเครื่องมือ (workspace = โฟลเดอร์โปรเจกต์)")
    parser.add_argument("--allow-run", action="store_true", help="อนุญาต run_command/run_python และการแก้ไฟล์ระหว่าง eval")
    parser.add_argument("--workspace-template", default=None,
                        help="โฟลเดอร์ต้นแบบ (เช่น evals/fixtures/edit) คัดลอกเป็น workspace ใหม่ทุกข้อ ใช้กับ eval การแก้ไฟล์")
    parser.add_argument("--pipeline", action="store_true", help="reviewer: ใช้ pipeline (analyzer, ตรวจ, ตรวจซ้ำ) แทนรอบเดียว")
    parser.add_argument("--thorough", action="store_true", help="ใช้คู่กับ --pipeline: ตรวจ 4 รอบแยกหัวข้อ")
    parser.add_argument("--judge", action="store_true", help="ให้โมเดลกรรมการให้คะแนน 0-2 ทุกคำตอบ")
    parser.add_argument("--judge-model", default="qwen3:14b", help="โมเดลกรรมการ (ค่าเริ่มต้น qwen3:14b)")
    parser.add_argument("--repeat", type=int, default=1, help="รันแต่ละข้อกี่รอบ เพื่อวัดความนิ่ง")
    args = parser.parse_args()

    console = Console()
    prof = profiles.load(args.profile)
    cases = load_cases(args.set or args.profile)
    rag = not args.no_rag
    models = [m.strip() for m in args.models.split(",")] if args.models else [args.model or prof.model or config.MODEL]

    out_dir = EVALS_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = "rag" if rag else "norag"

    summaries: list[dict] = []
    for model in models:
        records = run(args.profile, model, cases, rag=rag, show=args.show, console=console,
                      use_tools=args.tools, allow_run=args.allow_run, use_pipeline=args.pipeline, thorough=args.thorough,
                      judge_model=args.judge_model if args.judge else None, repeat=args.repeat,
                      workspace_template=Path(args.workspace_template) if args.workspace_template else None)
        safe = model.replace(":", "_").replace("/", "_")
        mode = ("-tools" if args.tools else ("-thorough" if args.pipeline and args.thorough else ("-pipeline" if args.pipeline else ""))) + (f"-x{args.repeat}" if args.repeat > 1 else "")
        out = out_dir / f"{args.set or args.profile}-{tag}{mode}-{safe}-{stamp}.jsonl"
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
        console.print(f"[dim]บันทึกผลที่ {out.relative_to(config.ROOT)}[/]\n")
        summaries.append(summary(records))

    if len(models) > 1:
        table = Table(title=f"เทียบโมเดลบน eval {args.profile} ({tag})")
        table.add_column("โมเดล", style="cyan")
        table.add_column("ผ่าน", justify="right")
        table.add_column("กรรมการ", justify="right")
        table.add_column("เฉลี่ย/ข้อ", justify="right")
        table.add_column("ช้าสุด", justify="right")
        table.add_column("ข้อที่ตก")
        for sm in summaries:
            table.add_row(
                sm["model"], f"{sm['passed']}/{sm['cases']}", "-" if sm["judge_avg"] is None else f"{sm['judge_avg']:.2f}", f"{sm['avg_seconds']}s",
                f"{sm['max_seconds']}s", ", ".join(sm["failed_ids"]) or "-",
            )
        console.print(table)
        out = out_dir / f"compare-{args.profile}-{tag}-{stamp}.json"
        out.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]บันทึกสรุปที่ {out.relative_to(config.ROOT)}[/]")

    if dashboard.refresh_file(force=True):
        console.print("[dim]อัปเดต reports/dashboard.html แล้ว[/]")


if __name__ == "__main__":
    main()
