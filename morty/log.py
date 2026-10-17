"""บันทึกการใช้งานจริงเป็น JSONL เพื่อนำกลับมาเป็น eval และวัตถุดิบ fine-tune

ไฟล์อยู่ที่ logs/YYYY-MM-DD.jsonl (ไม่ commit) หนึ่งบรรทัดต่อหนึ่งเหตุการณ์ append อย่างเดียว
  {"type": "exchange", "id": ..., "ts": ..., "source": "chat|review", "profile": ..., "model": ...,
   "question": ..., "answer": ..., "seconds": ..., "refs": [...]}
  {"type": "rating", "ref": <id ของ exchange>, "rating": "good|bad", "note": "..."}

ปิดการบันทึกได้ด้วย MORTY_LOG=0 ใน .env

ใช้งาน:  .venv/bin/python -m morty.log show            # 20 รายการล่าสุด
         .venv/bin/python -m morty.log bad             # เฉพาะที่ติดธง bad
         .venv/bin/python -m morty.log to-eval dev     # แปลงเคส bad ของ profile dev เป็นบรรทัด eval ให้ไปเติมคำตอบที่คาดหวัง
         .venv/bin/python -m morty.log stats
         .venv/bin/python -m morty.log rate <id> bad "เหตุผล"   # ให้คะแนนผล review ย้อนหลัง
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from . import config

LOG_DIR = config.ROOT / "logs"
ENABLED = os.getenv("MORTY_LOG", "1") != "0"


def _today_file() -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"


def _append(event: dict) -> None:
    if not ENABLED:
        return
    with _today_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def record(*, source: str, profile: str, model: str, question: str, answer: str,
           seconds: float, refs: list[str] | None = None, tools: list[str] | None = None,
           meta: dict | None = None) -> str:
    """บันทึกคู่ถามตอบ คืน id ไว้ใช้ให้คะแนนภายหลัง

    refs = เอกสาร RAG ที่ใช้, tools = ชื่อเครื่องมือที่โมเดลเรียก (ซ้ำได้), meta = ข้อมูลเพิ่ม เช่น ผล pipeline
    """
    event_id = uuid.uuid4().hex[:12]
    _append({
        "type": "exchange", "id": event_id, "ts": datetime.now().isoformat(timespec="seconds"),
        "source": source, "profile": profile, "model": model,
        "question": question, "answer": answer, "seconds": round(seconds, 2), "refs": refs or [],
        "tools": tools or [], "meta": meta or {},
    })
    _refresh_dashboard()
    return event_id


def rate(event_id: str, rating: str, note: str = "") -> None:
    _append({"type": "rating", "ref": event_id, "ts": datetime.now().isoformat(timespec="seconds"),
             "rating": rating, "note": note})
    _refresh_dashboard()


def _refresh_dashboard() -> None:
    """สร้าง reports/dashboard.html ใหม่ถ้าผู้ใช้เคยสร้างไว้ (import ตรงนี้เพื่อเลี่ยง import วน)"""
    try:
        from . import dashboard

        dashboard.refresh_file()
    except Exception:  # noqa: BLE001
        pass


# ---------- อ่านกลับ ----------

def load_all() -> list[dict]:
    events: list[dict] = []
    if not LOG_DIR.is_dir():
        return events
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def exchanges(with_ratings: bool = True) -> list[dict]:
    """รวม rating ล่าสุดเข้ากับ exchange แต่ละรายการ"""
    events = load_all()
    ratings = {e["ref"]: e for e in events if e.get("type") == "rating"}
    out = []
    for e in events:
        if e.get("type") != "exchange":
            continue
        r = ratings.get(e["id"])
        out.append({**e, "rating": r["rating"] if r else None, "note": r.get("note", "") if r else ""})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="ดู log การใช้งาน")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_show = sub.add_parser("show", help="รายการล่าสุด")
    p_show.add_argument("-n", type=int, default=20)
    p_show.add_argument("--profile", default=None)
    sub.add_parser("bad", help="เฉพาะที่ติดธง bad")
    p_eval = sub.add_parser("to-eval", help="แปลงเคส bad เป็นบรรทัด eval")
    p_eval.add_argument("profile")
    sub.add_parser("stats", help="สรุปจำนวนต่อ profile และโมเดล")
    p_rate = sub.add_parser("rate", help="ให้คะแนนรายการตาม id (ใช้กับผล review)")
    p_rate.add_argument("id")
    p_rate.add_argument("rating", choices=["good", "bad"])
    p_rate.add_argument("note", nargs="?", default="")
    args = parser.parse_args()

    if args.cmd == "rate":
        if not any(e.get("id") == args.id for e in load_all()):
            raise SystemExit(f"ไม่พบ id {args.id}")
        rate(args.id, args.rating, args.note)
        print(f"บันทึกแล้ว {args.id} = {args.rating}")
        return

    rows = exchanges()
    if args.cmd == "show":
        if args.profile:
            rows = [r for r in rows if r["profile"] == args.profile]
        for r in rows[-args.n:]:
            flag = {"bad": "✗", "good": "✓"}.get(r["rating"], " ")
            q = r["question"].replace("\n", " ")[:70]
            print(f"{flag} {r['ts']} {r['source']:6s} {r['profile']:9s} {r['model']:17s} {r['seconds']:5.1f}s  {q}")
    elif args.cmd == "bad":
        for r in rows:
            if r["rating"] == "bad":
                print(f"== {r['id']} {r['ts']} {r['profile']} {r['model']}  หมายเหตุ: {r['note'] or '-'}")
                print("ถาม:", r["question"][:300])
                print("ตอบ:", r["answer"][:300].replace("\n", " "), "\n")
    elif args.cmd == "to-eval":
        n = 0
        for r in rows:
            if r["rating"] == "bad" and r["profile"] == args.profile:
                case = {"id": f"log-{r['id']}", "q": r["question"], "expect_any": ["<เติมคำที่คำตอบที่ถูกต้องควรมี>"],
                        "thai": True, "note": r["note"] or "จาก log การใช้งานจริง"}
                print(json.dumps(case, ensure_ascii=False))
                n += 1
        if n == 0:
            print(f"# ยังไม่มีเคส bad ของ profile {args.profile}")
        else:
            print(f"# {n} เคส  เติม expect_any ให้ครบแล้วต่อท้ายไฟล์ evals/{args.profile}.jsonl", flush=True)
    elif args.cmd == "stats":
        from collections import Counter
        by = Counter((r["profile"], r["model"]) for r in rows)
        bad = Counter((r["profile"], r["model"]) for r in rows if r["rating"] == "bad")
        good = Counter((r["profile"], r["model"]) for r in rows if r["rating"] == "good")
        print(f"ทั้งหมด {len(rows)} รายการ")
        for (p, m), n in sorted(by.items()):
            print(f"  {p:9s} {m:17s} {n:4d} รายการ  good {good[(p, m)]:3d}  bad {bad[(p, m)]:3d}")


if __name__ == "__main__":
    main()
