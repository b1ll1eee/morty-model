"""โหลดค่าตั้งค่าจากไฟล์ .env"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL = os.getenv("MORTY_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
SYSTEM_PROMPT = os.getenv(
    "MORTY_SYSTEM_PROMPT",
    "คุณคือ Morty ผู้ช่วย AI ที่ตอบเป็นภาษาไทยอย่างกระชับและถูกต้อง",
)
# profile เริ่มต้น (ดูรายชื่อใน morty/profiles/)
PROFILE = os.getenv("MORTY_PROFILE", "general")
