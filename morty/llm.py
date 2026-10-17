"""ชั้นกลางสำหรับคุยกับโมเดล

ทุกส่วนของโปรเจกต์เรียกผ่านไฟล์นี้เท่านั้น
ถ้าวันหน้าจะย้ายจาก Ollama ไป Claude API ก็แก้ที่ไฟล์นี้ไฟล์เดียว
"""
from collections.abc import Iterator

import ollama

from . import config

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}

_client = ollama.Client(host=config.OLLAMA_HOST)


def chat(messages: list[Message], model: str | None = None, **options) -> str:
    """ส่งบทสนทนาไปที่โมเดล แล้วคืนคำตอบทั้งก้อน"""
    resp = _client.chat(model=model or config.MODEL, messages=messages, options=options)
    return resp["message"]["content"]


def chat_stream(messages: list[Message], model: str | None = None, **options) -> Iterator[str]:
    """เหมือน chat() แต่ทยอยคืนข้อความทีละชิ้นขณะโมเดลกำลังพิมพ์"""
    stream = _client.chat(
        model=model or config.MODEL, messages=messages, options=options, stream=True
    )
    for chunk in stream:
        yield chunk["message"]["content"]


def ask(prompt: str, system: str | None = None, **kw) -> str:
    """ทางลัด: ถามคำถามเดียว ได้คำตอบเดียว"""
    messages: list[Message] = [{"role": "system", "content": system or config.SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **kw)


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    """แปลงข้อความเป็นเวกเตอร์ สำหรับทำ RAG / ค้นหาความหมายในอนาคต"""
    resp = _client.embed(model=model, input=texts)
    return resp["embeddings"]


def list_models() -> list[str]:
    """รายชื่อโมเดลที่ดาวน์โหลดไว้ในเครื่อง"""
    return [m["model"] for m in _client.list()["models"]]
