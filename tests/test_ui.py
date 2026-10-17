import asyncio
from pathlib import Path

from textual.widgets import Static

from morty import ui


def _run(coro):
    return asyncio.run(coro)


def test_app_mounts_and_handles_commands(tmp_path, monkeypatch):
    async def inner():
        app = ui.MortyApp("general", Path(tmp_path), use_tools=False)
        async with app.run_test() as pilot:
            assert "Morty (กลาง)" in str(app.query_one("#status", Static).render())
            app.handle_command("/profile")
            app.handle_command("/profile dev")
            assert app.profile.name == "dev" and len(app.history) == 1
            app.handle_command("/model qwen3:14b")
            assert app.model == "qwen3:14b"
            app.handle_command("/bad ไม่ดี")  # ยังไม่มีคำตอบ ต้องไม่พัง
            app.handle_command("/nope")
            await pilot.pause()
            texts = [str(w.render()) for w in app.query(".system, .error")]
            assert any("profile ที่มี" in t for t in texts) and any("ไม่รู้จักคำสั่ง" in t for t in texts)
    _run(inner())


def test_streaming_answer_is_rendered_and_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.llm, "chat_stream", lambda messages, model=None, **o: iter(["สวัส", "ดี **ครับ**"]))
    recorded = {}
    monkeypatch.setattr(ui.log, "record", lambda **kw: recorded.update(kw) or "id1")

    async def inner():
        app = ui.MortyApp("general", Path(tmp_path), use_tools=False)
        async with app.run_test() as pilot:
            inp = app.query_one("#input")
            inp.value = "ทักทายหน่อย"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if not app.busy and recorded:
                    break
            assert recorded["answer"] == "สวัสดี **ครับ**" and recorded["source"] == "chat"
            assert app.history[-1]["role"] == "assistant" and app.last_id == "id1"
            app.handle_command("/good")
    _run(inner())


def test_permission_screen_dismisses():
    async def inner():
        app = ui.MortyApp("general", Path("."), use_tools=False)
        async with app.run_test() as pilot:
            results = []
            app.push_screen(ui.PermissionScreen("แก้ไฟล์ a.py:\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y"), results.append)
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert results == [True]
            app.push_screen(ui.PermissionScreen("รันคำสั่ง: rm -rf build"), results.append)
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert results == [True, False]
    _run(inner())
