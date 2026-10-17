"""ตั้งค่าร่วมของเทสต์: ไม่แตะ Ollama จริง ไม่เขียน log ลงโปรเจกต์"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MORTY_LOG", "0")


@pytest.fixture
def workspace(tmp_path: Path):
    """โฟลเดอร์จำลองสำหรับเครื่องมือ"""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def hello():\n    return 'hi'\n\nVALUE = 42\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n\nบรรทัดสอง\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_chat(monkeypatch):
    """แทน llm.chat / llm.chat_raw ด้วยคิวคำตอบที่กำหนดไว้ล่วงหน้า"""
    from morty import llm

    queue: list = []
    calls: list[dict] = []

    def _chat_raw(messages, model=None, tools=None, **options):
        calls.append({"messages": messages, "model": model, "tools": tools, "options": options})
        item = queue.pop(0)
        return item if isinstance(item, dict) else {"role": "assistant", "content": item}

    def _chat(messages, model=None, **options):
        return _chat_raw(messages, model=model, **options)["content"]

    monkeypatch.setattr(llm, "chat_raw", _chat_raw)
    monkeypatch.setattr(llm, "chat", _chat)
    return queue, calls
