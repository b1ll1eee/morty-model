from morty import reviewpipe as rp


def test_parse_findings_accepts_bullets_and_case():
    text = "- [bug] บรรทัด 3: a\n* [RISK] บรรทัด 5: b\n[style] บรรทัด 9: c\nบรรทัดธรรมดา\nไม่พบ"
    found = rp.parse_findings(text, "model:x")
    assert [(f.severity, f.text) for f in found] == [("bug", "บรรทัด 3: a"), ("risk", "บรรทัด 5: b"), ("style", "บรรทัด 9: c")]


def test_merge_dedupes_same_line_and_keeps_worst():
    fs = [
        rp.Finding("bug", "บรรทัด 4: undefined db", "ruff:F821", verified=True),
        rp.Finding("bug", "บรรทัด 4: db ไม่ได้ประกาศ", "model:correctness"),
        rp.Finding("risk", "บรรทัด 7: injection", "ruff:S605", verified=True),
        rp.Finding("style", "บรรทัด 7: ใช้ subprocess", "model:style"),
        rp.Finding("style", "บรรทัด 2-7: ชื่อฟังก์ชัน", "model:style"),
        rp.Finding("risk", "ไม่มีเลขบรรทัด", "model:security"),
    ]
    out = rp.merge(fs)
    assert [(f.severity, f.source) for f in out] == [
        ("bug", "ruff:F821"), ("risk", "ruff:S605"), ("style", "model:style"), ("risk", "model:security")]


def test_verify_regex():
    assert rp.VERIFY_RE.match("1: KEEP - อินพุต []").group(2) == "KEEP"
    assert rp.VERIFY_RE.match("2) drop เพราะ").group(2).upper() == "DROP"
    assert rp.VERIFY_RE.match("3. DOWNGRADE").group(2) == "DOWNGRADE"
    assert rp.VERIFY_RE.match("ข้อ 1 KEEP") is None


def test_report_render_and_verdict():
    r = rp.Report(findings=[rp.Finding("style", "บรรทัด 1: x", "model:style")], passes=4)
    assert r.verdict().endswith("ผ่าน")
    r.findings.append(rp.Finding("bug", "บรรทัด 2: y", "ruff:F821", verified=True))
    text = r.render()
    assert "ต้องแก้ก่อน merge" in text
    assert text.index("[bug]") < text.index("[style]")
    assert "(ruff:F821)" in text


def test_report_empty_says_clean():
    assert "ไม่พบปัญหาร้ายแรง" in rp.Report(passes=4).render()


def test_analyze_snippet_catches_planted_bugs():
    code = "import os\ndef save(order):\n    try:\n        db.commit(order)\n    except:\n        pass\n    return os.system('echo ' + order)\n"
    found = rp.analyze_snippet(code, "python")
    sources = {f.source for f in found}
    assert "ruff:F821" in sources           # db ไม่ได้ประกาศ
    assert any(s.startswith(("ruff:S605", "bandit:B605")) for s in sources)  # shell injection
    assert all(f.verified for f in found)


def test_analyze_snippet_ignores_non_python():
    assert rp.analyze_snippet("console.log(1)", "javascript") == []


def test_pipeline_with_fake_model(fake_chat):
    queue, calls = fake_chat
    code = "def f(xs):\n    return xs[0]\n\ndef g(d):\n    return d['k']\n"
    prompt = f"ทบทวนโค้ดนี้:\n```python\n{code}```"
    queue += [
        "[bug] บรรทัด 2: xs ว่างจะ IndexError ควรเช็คก่อน",   # correctness
        "ไม่พบ",                                              # security
        "[risk] บรรทัด 5: d อาจไม่มี key k",                   # robustness (คนละบรรทัดกับ bug จึงไม่ถูก merge)
        "[style] บรรทัด 1: ควรใส่ type hint",                 # style
        "1: KEEP - xs = []\n2: DROP - สมมติเกินโค้ดที่เห็น",     # verify
    ]
    report = rp.review(prompt, snippet=(code, "python"), model="m")
    assert report.passes == 4 and report.verified_dropped == 1
    sev = sorted(f.severity for f in report.findings)
    assert sev == ["bug", "style"]
    assert report.findings[0].verified and "xs = []" in report.findings[0].note
    assert calls[-1]["options"]["temperature"] == 0.0


def test_parse_findings_json_and_fallback():
    js = '{"findings": [{"severity": "bug", "line": "3", "problem": "IndexError เมื่อ xs ว่าง", "why": "xs[0]", "fix": "เช็คก่อน"}, {"severity": "nope", "line": "1", "problem": "x"}]}'
    out = rp.parse_findings(js, "model:x")
    assert len(out) == 1 and out[0].severity == "bug" and out[0].text.startswith("บรรทัด 3: IndexError")
    assert "ควรแก้: เช็คก่อน" in out[0].text
    assert rp.parse_findings('{"findings": []}', "m") == []
    assert rp.parse_findings("[risk] บรรทัด 2: y", "m")[0].severity == "risk"


def test_parse_decisions_json_and_fallback():
    assert rp.parse_decisions('{"decisions": [{"n": 1, "verdict": "keep", "reason": "xs=[]"}, {"n": 2, "verdict": "DROP"}]}') == {1: ("KEEP", "xs=[]"), 2: ("DROP", "")}
    assert rp.parse_decisions("1: DOWNGRADE - เล็กน้อย") == {1: ("DOWNGRADE", "เล็กน้อย")}
