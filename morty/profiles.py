"""โหลด profile ของผู้ช่วยจาก morty/profiles/*.toml

profile คือชั้นที่ซ้อนบนโมเดลกลางตัวเดียวกัน แต่ละตัวกำหนด
system prompt, พารามิเตอร์การสุ่ม และโฟลเดอร์เอกสารสำหรับ RAG
เพิ่ม expert ใหม่ได้โดยเพิ่มไฟล์ .toml หนึ่งไฟล์ ไม่ต้องแก้โค้ด
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
DEFAULT = config.PROFILE


@dataclass(frozen=True)
class Profile:
    name: str
    title: str
    system: str
    model: str | None = None  # None = ใช้ค่าจาก .env
    options: dict = field(default_factory=dict)  # temperature, num_ctx, ...
    data_dir: Path | None = None  # เอกสารสำหรับ RAG
    rag: dict = field(default_factory=dict)  # enabled, top_k, min_score
    tools: dict = field(default_factory=dict)  # enabled, allow, confirm_run


def names() -> list[str]:
    """รายชื่อ profile ที่มี เรียงตามตัวอักษร"""
    return sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))


def load(name: str) -> Profile:
    """อ่าน profile ตามชื่อไฟล์ เช่น load("dev") -> morty/profiles/dev.toml"""
    path = PROFILES_DIR / f"{name}.toml"
    if not path.is_file():
        raise KeyError(f"ไม่พบ profile '{name}' (ที่มี: {', '.join(names())})")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    data_dir = raw.get("data_dir")
    return Profile(
        name=name,
        title=raw.get("title", name),
        system=raw["system"].strip(),
        model=raw.get("model") or None,
        options=dict(raw.get("options", {})),
        data_dir=(config.ROOT / data_dir) if data_dir else None,
        rag=dict(raw.get("rag", {})),
        tools=dict(raw.get("tools", {})),
    )
