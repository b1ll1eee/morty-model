"""ชั้นกลางสำหรับคุยกับโมเดล

ทุกส่วนของโปรเจกต์เรียกผ่านไฟล์นี้เท่านั้น
ถ้าวันหน้าจะย้ายจาก Ollama ไป Claude API ก็แก้ที่ไฟล์นี้ไฟล์เดียว
"""
from collections.abc import Iterator

import ollama

from . import config, profiles

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}

_client = ollama.Client(host=config.OLLAMA_HOST)


def _split(options: dict) -> tuple[dict, dict]:
    """แยกพารามิเตอร์ระดับคำขอ (เช่น think) ออกจาก options ของโมเดล (temperature, num_ctx ...)"""
    request = {}
    if "think" in options:
        # โมเดลตระกูล qwen3 มีโหมดคิดยาว think=False ปิดเพื่อให้ตอบเร็ว โมเดลอื่นเพิกเฉยค่านี้
        request["think"] = options.pop("think")
    return request, options


def chat(messages: list[Message], model: str | None = None, **options) -> str:
    """ส่งบทสนทนาไปที่โมเดล แล้วคืนคำตอบทั้งก้อน"""
    request, options = _split(dict(options))
    resp = _client.chat(model=model or config.MODEL, messages=messages, options=options, **request)
    return resp["message"]["content"]


def chat_raw(messages: list[dict], model: str | None = None, tools: list[dict] | None = None, **options) -> dict:
    """ส่งบทสนทนา (รองรับ tool calling) คืน message ทั้งก้อนเป็น dict: content, tool_calls, ..."""
    request, options = _split(dict(options))
    if tools:
        request["tools"] = tools
    resp = _client.chat(model=model or config.MODEL, messages=messages, options=options, **request)
    msg = resp["message"]
    out = {"role": msg["role"], "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [
            {"function": {"name": tc["function"]["name"], "arguments": dict(tc["function"]["arguments"] or {})}}
            for tc in msg["tool_calls"]
        ]
    return out


def chat_stream(messages: list[Message], model: str | None = None, **options) -> Iterator[str]:
    """เหมือน chat() แต่ทยอยคืนข้อความทีละชิ้นขณะโมเดลกำลังพิมพ์"""
    request, options = _split(dict(options))
    stream = _client.chat(
        model=model or config.MODEL, messages=messages, options=options, stream=True, **request
    )
    for chunk in stream:
        yield chunk["message"]["content"]


def ask(
    prompt: str, system: str | None = None, profile: str | None = None, rag: bool = True, **kw
) -> str:
    """ทางลัด: ถามคำถามเดียว ได้คำตอบเดียว

    profile="dev" จะใช้ system prompt, โมเดล และพารามิเตอร์ของ profile นั้น
    และถ้า profile นั้นมีดัชนี RAG จะแนบเอกสารที่เกี่ยวข้องไปด้วย (ปิดได้ด้วย rag=False)
    ค่าที่ส่งมาตรงๆ (system=, model=, temperature=) มีสิทธิ์เหนือกว่า profile เสมอ
    """
    options: dict = {}
    if profile:
        from . import rag as _rag  # import ตรงนี้เพื่อเลี่ยง import วน

        p = profiles.load(profile)
        system = system or p.system
        options = {**p.options, "model": p.model}
        if rag:
            prompt, _ = _rag.augment(p, prompt)
    options.update(kw)
    messages: list[Message] = [{"role": "system", "content": system or config.SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **options)


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    """แปลงข้อความเป็นเวกเตอร์ สำหรับทำ RAG / ค้นหาความหมายในอนาคต"""
    resp = _client.embed(model=model, input=texts)
    return resp["embeddings"]


def list_models() -> list[str]:
    """รายชื่อโมเดลที่ดาวน์โหลดไว้ในเครื่อง"""
    return [m["model"] for m in _client.list()["models"]]
