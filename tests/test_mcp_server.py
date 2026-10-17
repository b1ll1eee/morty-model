import asyncio

from morty import mcp_server


def test_tools_registered():
    names = [t.name for t in asyncio.run(mcp_server.server.list_tools())]
    assert names == ["morty_ask", "morty_review", "morty_search_docs", "morty_profiles"]


def test_profiles_tool_lists_all(monkeypatch):
    monkeypatch.setattr(mcp_server.rag, "available", lambda p: False)
    out = mcp_server.morty_profiles()
    assert "dev:" in out and "reviewer: โมเดล qwen3:14b" in out


def test_ask_rejects_unknown_profile():
    assert "ไม่มี profile" in mcp_server.morty_ask("x", profile="nope")


def test_review_requires_input():
    assert "ต้องส่ง" in mcp_server.morty_review()
    assert "ไม่พบไฟล์" in mcp_server.morty_review(path="no/such/file.py")


def test_review_snippet_uses_single_review(monkeypatch):
    monkeypatch.setattr(mcp_server.reviewpipe, "single_review",
                        lambda prompt, **kw: ("- พบ 0 bug: ผ่าน", mcp_server.reviewpipe.Report(passes=1)))
    recorded = {}
    monkeypatch.setattr(mcp_server.log, "record", lambda **kw: recorded.update(kw) or "id")
    out = mcp_server.morty_review(code="print(1)")
    assert out.startswith("- พบ 0 bug") and recorded["source"] == "mcp" and recorded["meta"]["tool"] == "morty_review"
