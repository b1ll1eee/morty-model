# คู่มือโปรเจกต์ morty-model สำหรับนักพัฒนา

เอกสารนี้เป็นตัวอย่างเอกสารใน `data/dev/` ที่ morty-dev ใช้ตอบคำถามเกี่ยวกับโปรเจกต์นี้เอง
ใส่ไฟล์อื่นเพิ่มได้ตามต้องการ เช่น docs ของทีม, coding convention, สรุป API ภายใน
แล้วรัน `python -m morty.rag index dev` เพื่ออัปเดตดัชนี

## โครงสร้างโค้ด

- `morty/config.py` อ่านค่าจาก `.env` ได้แก่ `MORTY_MODEL`, `OLLAMA_HOST`, `MORTY_SYSTEM_PROMPT`, `MORTY_PROFILE`
- `morty/llm.py` เป็นชั้นเดียวที่คุยกับโมเดล มีฟังก์ชัน `chat`, `chat_stream`, `ask`, `embed`, `list_models`
  ถ้าจะย้ายจาก Ollama ไป Claude API ให้แก้ไฟล์นี้ไฟล์เดียว
- `morty/profiles.py` โหลด profile จากไฟล์ TOML ใน `morty/profiles/`
- `morty/rag.py` ทำดัชนีเอกสารและค้นหาท่อนที่เกี่ยวข้อง ดัชนีเก็บที่ `data/<profile>/.morty-index.json`
- `morty/chat.py` แชท CLI รองรับ `--profile`, `/profile`, `/model`, `/reset`
- `morty/evaluate.py` รัน eval set จาก `evals/<profile>.jsonl`

## วิธีเพิ่ม expert ใหม่

1. สร้างไฟล์ `morty/profiles/<ชื่อ>.toml` กำหนด `title`, `system`, `[options]` และถ้าต้องการ RAG ให้ใส่ `data_dir = "data/<ชื่อ>"` กับ `[rag]`
2. สร้างโฟลเดอร์ `data/<ชื่อ>/` แล้วใส่เอกสาร จากนั้นรัน `python -m morty.rag index <ชื่อ>`
3. สร้าง `evals/<ชื่อ>.jsonl` อย่างน้อย 10 ข้อ แล้วรัน `python -m morty.evaluate <ชื่อ>`
4. ไม่ต้องแก้โค้ด Python ใดๆ ระบบจะเห็น profile ใหม่จากชื่อไฟล์โดยอัตโนมัติ

## กติกาของทีม

- โมเดลเริ่มต้นคือ `qwen2.5:7b` เปลี่ยนได้ที่ `MORTY_MODEL` ใน `.env` หรือ `model =` ใน profile
- ทุกครั้งที่แก้ system prompt หรือสลับโมเดล ต้องรัน `python -m morty.evaluate dev` ซ้ำ และคะแนนต้องไม่ต่ำกว่าเดิม
- ไฟล์ `.env` และโฟลเดอร์ `evals/results/` ไม่ commit ส่วน `data/` ไม่ commit ยกเว้นไฟล์ตัวอย่างนี้
- ค่า RAG ของ dev: `top_k = 4`, `min_score = 0.6` ปรับได้ใน `morty/profiles/dev.toml`
- embedding ใช้โมเดล `nomic-embed-text` ซึ่งต้องใส่ prefix `search_document:` ตอนทำดัชนี และ `search_query:` ตอนค้น
