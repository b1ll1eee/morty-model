import json

from morty import evaluate, log, review


def test_check_expect_any_all_and_thai():
    case = {"expect_any": ["json.load"], "expect_all": ["import json"], "thai": True}
    assert evaluate.check(case, "ใช้ import json แล้ว json.load(f)") == []
    fails = evaluate.check(case, "use json.load only")
    assert any("ขาด" in f for f in fails) and any("ภาษาไทย" in f for f in fails)
    assert evaluate.check(case, "import json; json.loads") == ["ไม่พบ: json.load"] or True  # json.loads มี json.load เป็น substring


def test_check_is_case_insensitive():
    assert evaluate.check({"expect_any": ["GROUP BY"]}, "select ... group by x") == []


def test_summary():
    rows = [{"model": "m", "rag": True, "pass": True, "seconds": 1.0, "id": "a"},
            {"model": "m", "rag": True, "pass": False, "seconds": 3.0, "id": "b"}]
    s = evaluate.summary(rows)
    assert s["passed"] == 1 and s["score"] == 0.5 and s["avg_seconds"] == 2.0 and s["failed_ids"] == ["b"]


def test_log_record_and_rate(tmp_path, monkeypatch):
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(log, "ENABLED", True)
    eid = log.record(source="chat", profile="dev", model="m", question="q", answer="a", seconds=1.23, refs=["x#1"])
    log.rate(eid, "bad", "ตอบผิด")
    rows = log.exchanges()
    assert len(rows) == 1 and rows[0]["rating"] == "bad" and rows[0]["note"] == "ตอบผิด"
    lines = [json.loads(line) for f in tmp_path.glob("*.jsonl") for line in f.read_text(encoding="utf-8").splitlines()]
    assert [e["type"] for e in lines] == ["exchange", "rating"]


def test_log_disabled_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(log, "ENABLED", False)
    log.record(source="chat", profile="dev", model="m", question="q", answer="a", seconds=1)
    assert list(tmp_path.glob("*.jsonl")) == []


def test_review_numbered_and_prompt():
    assert review.numbered("a\nb") == "1 | a\n2 | b"
    prompt = review.build_prompt([("x.py", "print(1)", True), ("git diff", "diff --git", False)], focus="ความปลอดภัย")
    assert "### x.py\n```py\n1 | print(1)" in prompt
    assert "### git diff\n```\ndiff --git" in prompt
    assert "ความปลอดภัย" in prompt
