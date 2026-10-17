# Morty

ผู้ช่วย AI ที่รันในเครื่องคุณเอง ฟรี ไม่ส่งข้อมูลออกไปไหน
โมเดลกลางหนึ่งตัวบน [Ollama](https://ollama.com) แตกเป็น expert หลายด้านด้วยไฟล์ตั้งค่าไฟล์เดียว ไม่ต้องเทรนใหม่

```
คุณ> ในโปรเจกต์นี้ถ้าจะเพิ่ม expert ใหม่ต้องทำยังไง
อ้างอิง: morty-project.md#2 (0.75)
morty-dev> สร้างไฟล์ morty/profiles/<ชื่อ>.toml กำหนด title, system และ [options] ...
```

## ทำอะไรได้บ้าง

| | profile | เหมาะกับ |
|---|---|---|
| 💬 | `general` | ถามตอบทั่วไป สรุปความ ร่างข้อความ |
| 🛠 | `dev` | เขียนโค้ด แก้บั๊ก ตอบจากเอกสารของทีมผ่าน RAG |
| 🔍 | `reviewer` | ตรวจโค้ดหรือ `git diff` หาบั๊กและช่องโหว่ก่อน merge |

ทุก profile ใช้โมเดลกลางเดียวกัน (`qwen2.5:7b` ราว 5 GB) ต่างกันที่ system prompt, พารามิเตอร์ และเอกสารที่ค้นมาใส่ใน context
ยกเว้น `reviewer` ที่ eval บอกว่าคุ้มจะใช้ `qwen3:14b` เพราะจับบั๊กที่ 7b พลาดได้ และงาน review ไม่ต้องการความเร็ว

## เริ่มใช้ใน 5 นาที

ต้องมี macOS Apple Silicon, Python 3.12 ขึ้นไป และพื้นที่ว่างราว 20 GB (รวม `qwen3:14b` สำหรับ reviewer ดึงเพิ่มด้วย `ollama pull qwen3:14b`)

```bash
brew install ollama && ollama serve &
ollama pull qwen2.5:7b && ollama pull nomic-embed-text

python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env

.venv/bin/python -m morty.chat --profile dev
```

## ใช้งานประจำวัน

```bash
.venv/bin/python -m morty.chat                        # แชทกับ Morty ตัวกลาง
.venv/bin/python -m morty.chat --profile dev          # ปรึกษาเรื่องโค้ด
git diff | .venv/bin/python -m morty.review           # ให้ reviewer ตรวจ diff ก่อน commit
.venv/bin/python -m morty.review app.py --focus ความปลอดภัย
```

ในแชทพิมพ์ `/profile reviewer` เพื่อสลับ expert กลางคัน หรือ `/model qwen3:14b` เพื่อสลับโมเดล
ถ้าคำตอบไหนไม่ดี พิมพ์ `/bad เหตุผล` ระบบจะติดธงไว้ใน log เพื่อนำกลับมาเป็น eval

เรียกจากโค้ดได้ด้วย

```python
from morty import llm
llm.ask("อ่านไฟล์ JSON ใน Python ยังไง", profile="dev")
```

## ทำงานอย่างไร

```mermaid
flowchart TB
    U[ผู้ใช้ หรือโค้ด Python] --> CLI["morty.chat / morty.review  --profile ชื่อ"]
    CLI --> G["general"]
    CLI --> D["dev<br/>+ RAG จาก data/dev/"]
    CLI --> R["reviewer<br/>รับโค้ดหรือ diff"]
    G & D & R --> L["morty/llm.py<br/>จุดเดียวที่คุยกับโมเดล"]
    L --> O["Ollama ในเครื่อง<br/>qwen2.5:7b"]
    L -.->|เมื่อจำเป็น| C["Claude API"]
    style L stroke:#0e7c6b,stroke-width:2px
```

ทุกคู่ถามตอบถูกบันทึกลง `logs/` ในเครื่อง คำตอบที่ติดธง `/bad` คือวัตถุดิบของ eval ข้อถัดไป
และทุกการเปลี่ยนแปลง ไม่ว่าแก้ prompt เพิ่มเอกสาร หรือสลับโมเดล ต้องผ่าน eval ชุดเดิมก่อน

```bash
.venv/bin/python -m morty.log bad                                       # ดูคำตอบที่ติดธงไว้
.venv/bin/python -m morty.log to-eval dev >> evals/dev.jsonl            # แปลงเป็นข้อสอบ แล้วเติมคำตอบที่คาดหวัง
.venv/bin/python -m morty.evaluate dev                                  # 12 ข้อ ผ่านกี่ข้อ ใช้เวลาเท่าไร
.venv/bin/python -m morty.evaluate dev --models qwen2.5:7b,qwen3:14b    # เทียบโมเดลในรอบเดียว
```

ผลล่าสุด (17 ต.ค. 2026)

| eval | `qwen2.5:7b` | `qwen3:14b` | ตัดสินใจ |
|---|---|---|---|
| dev 12 ข้อ | 12/12, 16s ต่อข้อ | 12/12, 42s ต่อข้อ | dev ใช้ 7b เร็วกว่าเกือบ 3 เท่าโดยคะแนนเท่ากัน |
| reviewer 11 ข้อ | 9/11, 11s ต่อข้อ | 10/11, 21s ต่อข้อ | reviewer ใช้ 14b เพราะจับ bare except และ secret ที่ฝังในโค้ดได้ |

RAG ทำให้ dev ขึ้นจาก 9/12 เป็น 12/12 ในคำถามเกี่ยวกับโปรเจกต์ รายละเอียดอยู่ใน `docs/reference.md`

## เพิ่ม expert ของคุณเอง

1. สร้าง `morty/profiles/<ชื่อ>.toml` ใส่ `title`, `system` และ `[options]` (คัดลอกจาก `dev.toml` ได้)
2. ถ้าอยากให้รู้เอกสารของคุณ ใส่ไฟล์ลง `data/<ชื่อ>/` แล้วรัน `python -m morty.rag index <ชื่อ>`
3. เขียน `evals/<ชื่อ>.jsonl` สัก 10 ข้อ แล้วรัน `python -m morty.evaluate <ชื่อ>`

ไม่ต้องแก้โค้ด Python แม้แต่บรรทัดเดียว

## โครงสร้าง

```
morty/
  llm.py          ชั้นเดียวที่คุยกับโมเดล ย้ายไป Claude API ได้โดยแก้ไฟล์นี้ไฟล์เดียว
  profiles/       general.toml  dev.toml  reviewer.toml
  rag.py          ทำดัชนีและค้นเอกสารใน data/<profile>/
  chat.py         แชท CLI
  review.py       ส่งไฟล์หรือ diff ให้ reviewer
  evaluate.py     รัน eval และเทียบโมเดล
  log.py          บันทึกการใช้งาน ให้คะแนน และแปลงเคสแย่เป็น eval
evals/            คำถามทดสอบต่อ profile
logs/             บันทึกการใช้งานจริงรายวัน (ไม่ commit)
data/dev/         เอกสารของทีมสำหรับ RAG (ไม่ commit)
docs/reference.md รายละเอียดการตั้งค่า RAG, eval, ผลเทียบโมเดล, ที่เก็บโมเดล
```

## แผนต่อไป

- ใช้งานจริง ติดธง `/bad` เมื่อตอบไม่ดี แล้วแปลงเป็น eval ด้วย `morty.log to-eval` และขยายเอกสารใน `data/dev/`
- เพิ่ม expert ด้านอื่น เช่น research เมื่อ dev และ reviewer นิ่งแล้ว
- LoRA fine-tune ด้วย MLX เมื่อมี log สะสมมากพอ และต้องชนะ profile + RAG บน eval เดิมเท่านั้น

หลักข้อเดียว: ไม่เปลี่ยนอะไรโดยไม่มี eval รองรับ และไม่ขึ้นชั้นที่แพงกว่าถ้าชั้นที่ถูกกว่ายังไม่ได้ลองจนสุด
