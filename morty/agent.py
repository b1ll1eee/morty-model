"""วงจร tool calling: ถามโมเดล ถ้าโมเดลขอใช้เครื่องมือก็รันแล้วส่งผลกลับ วนจนได้คำตอบ

ใช้โดย chat (profile ที่เปิด [tools]) และ evaluate --tools
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import llm, profiles, tools

MAX_ROUNDS = 8
Event = Callable[[str, str], None]  # (ชนิด, ข้อความ) เช่น ("tool", "read_file(path=...)")


def make_workspace(root: Path, profile: profiles.Profile, confirm: Callable[[str], bool]) -> tools.Workspace:
    allow = set(profile.tools.get("allow") or tools.ALL_TOOLS)
    return tools.Workspace(root=root.resolve(), confirm=confirm, allow=allow)


def run(messages: list[dict], *, model: str, options: dict, ws: tools.Workspace,
        on_event: Event | None = None) -> tuple[str, list[dict]]:
    """คืน (คำตอบสุดท้าย, ข้อความทั้งหมดที่เพิ่มระหว่างทาง รวม tool call และผลลัพธ์)"""
    schemas = tools.schemas_for(sorted(ws.allow))
    convo = list(messages)
    added: list[dict] = []
    for _ in range(MAX_ROUNDS):
        msg = llm.chat_raw(convo, model=model, tools=schemas, **options)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content", ""), added
        convo.append(msg)
        added.append(msg)
        for tc in calls:
            name = tc["function"]["name"]
            args = tc["function"].get("arguments") or {}
            if on_event:
                shown = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
                on_event("tool", f"{name}({shown})")
            result = tools.call(ws, name, dict(args))
            if on_event:
                on_event("result", result.splitlines()[0][:100] if result else "")
            tool_msg = {"role": "tool", "content": result, "tool_name": name}
            convo.append(tool_msg)
            added.append(tool_msg)
    # ครบจำนวนรอบแล้วยังไม่สรุป ขอให้ตอบจากที่มี
    convo.append({"role": "user", "content": "สรุปคำตอบจากข้อมูลที่ได้มาแล้ว ไม่ต้องใช้เครื่องมือเพิ่ม"})
    msg = llm.chat_raw(convo, model=model, **options)
    return msg.get("content", ""), added
