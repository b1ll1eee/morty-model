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
            app.handle_command("/profile dev")
            assert app.profile.name == "dev" and len(app.history) == 1
            app.handle_command("/model qwen3:14b")
            assert app.model == "qwen3:14b"
            app.handle_command("/bad ไม่ดี")  # ยังไม่มีคำตอบ ต้องไม่พัง
            app.handle_command("/nope")
            await pilot.pause()
            texts = [str(w.render()) for w in app.query(".system, .error")]
            assert any("สลับเป็น" in t for t in texts) and any("ไม่รู้จักคำสั่ง" in t for t in texts)
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


def test_input_history_up_down(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.log, "exchanges", lambda: [{"source": "chat", "question": "เก่าสุด"}, {"source": "chat", "question": "ล่าสุด"}])

    async def inner():
        app = ui.MortyApp("general", Path(tmp_path), use_tools=False)
        async with app.run_test() as pilot:
            inp = app.query_one("#input", ui.HistoryInput)
            assert inp.history == ["เก่าสุด", "ล่าสุด"]
            inp.value = "กำลังพิมพ์"
            await pilot.press("up")
            assert inp.value == "ล่าสุด"
            await pilot.press("up")
            assert inp.value == "เก่าสุด"
            await pilot.press("up")               # สุดแล้ว ค้างที่เดิม
            assert inp.value == "เก่าสุด"
            await pilot.press("down")
            assert inp.value == "ล่าสุด"
            await pilot.press("down")             # กลับมาที่ร่างที่พิมพ์ค้างไว้
            assert inp.value == "กำลังพิมพ์"
            inp.value = "/help"
            await pilot.press("enter")            # คำสั่งก็ถูกจำ และไม่จำซ้ำติดกัน
            inp.remember("/help")
            assert inp.history[-1] == "/help" and inp.history.count("/help") == 1 and inp.pos == len(inp.history)
    _run(inner())


def test_resolve_model_aliases():
    names = ["qwen2.5:7b", "qwen3:14b"]
    assert ui.resolve_model("qwen3:14b", names) == "qwen3:14b"
    assert ui.resolve_model("14b", names) == "qwen3:14b"
    assert ui.resolve_model("fast", names) == "qwen2.5:7b"
    assert ui.resolve_model("smart", names) == "qwen3:14b"
    assert ui.resolve_model("qwen", names) is None      # กำกวม
    assert ui.resolve_model("llama", names) is None     # ไม่มี


def test_model_picker_and_short_names(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "installed_models", lambda: [("qwen2.5:7b", "4.7 GB"), ("qwen3:14b", "9.3 GB")])

    async def inner():
        app = ui.MortyApp("general", Path(tmp_path), use_tools=False)
        async with app.run_test() as pilot:
            app.handle_command("/model 14b")
            assert app.model == "qwen3:14b"
            app.handle_command("/model nope")
            assert app.model == "qwen3:14b"
            app.handle_command("/model")               # เปิดรายการ ตัวปัจจุบันถูกไฮไลต์ กดขึ้นแล้ว Enter = เลือก 7b
            await pilot.pause()
            assert isinstance(app.screen, ui.PickerScreen)
            await pilot.press("up")
            await pilot.press("enter")
            await pilot.pause()
            assert app.model == "qwen2.5:7b"
            app.handle_command("/profile")
            await pilot.pause()
            await pilot.press("escape")                # ยกเลิก ไม่เปลี่ยน
            await pilot.pause()
            assert app.profile.name == "general"
    _run(inner())
