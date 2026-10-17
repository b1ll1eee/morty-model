# เอกสารอ้างอิง morty-model

รายละเอียดที่ README ตัดออกเพื่อความกระชับ อ่านเมื่อต้องปรับแต่งหรือแก้ปัญหา

## ติดตั้ง Ollama แบบไม่ใช้ Homebrew

```bash
mkdir -p ~/.local/ollama ~/.local/bin
curl -fsSL -o /tmp/ollama.tgz https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz
tar -xzf /tmp/ollama.tgz -C ~/.local/ollama
ln -sf ~/.local/ollama/ollama ~/.local/bin/ollama
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

## ที่เก็บโมเดล

Ollama เก็บ weight ไว้ที่ `~/.ollama/models` โดยปริยาย เครื่องนี้ย้ายไปดิสก์ DEV เพื่อไม่กินพื้นที่ในเครื่อง

```bash
echo 'export OLLAMA_MODELS="/Volumes/DEV/models/ollama"' >> ~/.zshrc && source ~/.zshrc
ollama serve &
```

โฟลเดอร์ข้างเคียง `/Volumes/DEV/models/lora/` และ `mlx/` สำรองไว้สำหรับ adapter และโมเดลแปลงรูปแบบในอนาคต
ตรวจความคืบหน้าดาวน์โหลดด้วย `du -h` บนไฟล์ `*-partial` ไม่ใช่ `ls -l` เพราะ Ollama จองขนาดเต็มไว้ก่อน

## ไฟล์ profile

แต่ละ profile เป็นไฟล์ TOML ใน `morty/profiles/`

| ฟิลด์ | ความหมาย |
|---|---|
| `title` | ชื่อที่แสดงในหน้าแชท |
| `system` | system prompt ของ expert ด้านนั้น |
| `model` | (ไม่บังคับ) ใช้โมเดลคนละตัวกับ `.env` เช่น `qwen3:14b` |
| `data_dir` | (ไม่บังคับ) โฟลเดอร์เอกสารสำหรับ RAG เช่น `data/dev` |
| `[options]` | พารามิเตอร์ส่งให้ Ollama: `temperature`, `num_ctx`, และ `think` (ปิดโหมดคิดยาวของ qwen3) |
| `[rag]` | `enabled`, `top_k` จำนวนท่อนที่แนบ, `min_score` ความคล้ายขั้นต่ำ |

ค่าที่ส่งตรงๆ ตอนรัน (`--model`, `--system`) มีสิทธิ์เหนือกว่าค่าใน profile เสมอ
ตั้ง profile เริ่มต้นได้ที่ `MORTY_PROFILE=` ใน `.env`

## RAG ทำงานอย่างไร

`morty/rag.py` ทำดัชนีไฟล์ข้อความและโค้ดใน `data/<profile>/` ด้วย `nomic-embed-text`
(ใส่ prefix `search_document:` ตอนทำดัชนี และ `search_query:` ตอนค้น ตามที่โมเดลต้องการ)

- ตัดเป็นท่อนละราว 1,200 ตัวอักษรตามย่อหน้า มี overlap 150 ตัวอักษร
- เก็บดัชนีที่ `data/<profile>/.morty-index.json` ไฟล์ที่ sha1 ไม่เปลี่ยนจะไม่คำนวณซ้ำ
- ตอนถาม ค้นท่อนที่คล้ายที่สุด `top_k` ท่อนที่คะแนนถึง `min_score` แนบไปกับคำถามพร้อมหมายเลข [n]
- แนบเฉพาะข้อความที่ส่งรอบนั้น ไม่เก็บลง history ของแชท เพื่อไม่ให้ context บวม
- cosine similarity คำนวณด้วย Python ล้วน พอสำหรับหลักพันท่อน ใหญ่กว่านั้นค่อยย้ายไป vector DB

เกณฑ์ `min_score` ของ dev คือ 0.6 เพราะวัดแล้ว nomic ให้คะแนน 0.45 ถึง 0.56 แม้คำถามไม่เกี่ยวกับเอกสารเลย
ลองดูคะแนนจริงได้ด้วย `python -m morty.rag search dev "คำถาม"`

## รูปแบบ eval

`evals/<profile>.jsonl` หนึ่งบรรทัดต่อหนึ่งข้อ

```json
{"id": "py-json", "q": "คำถาม", "expect_any": ["คำที่ควรมีอย่างน้อยหนึ่งคำ"], "expect_all": ["คำที่ต้องมีทุกคำ"], "thai": true}
```

การตรวจเป็นแบบหาคำในคำตอบ ไว้จับการถดถอยเวลาแก้ prompt หรือสลับโมเดล ไม่ได้แทนการอ่านคำตอบจริง
ผลแต่ละรอบเก็บที่ `evals/results/` (ไม่ commit) และ `--models a,b` จะสรุปตารางเทียบให้

## ผลเทียบโมเดล 17 ต.ค. 2026

MacBook Pro M4 24 GB, eval dev 12 ข้อ, RAG เปิด, ปิดโหมดคิดยาวของ qwen3
เวลาตัดข้อแรกออกเพราะรวมเวลาโหลด weight จากดิสก์

| โมเดล | ผ่าน | เฉลี่ยต่อข้อ | ช้าสุด | หมายเหตุ |
|---|---|---|---|---|
| `qwen2.5:7b` | 12/12 | 15.8s | 25s | โมเดลกลางปัจจุบัน |
| `qwen2.5-coder:7b` | 11/12 | 14.7s | 29s | หลุดตอบอังกฤษ ไม่แนะนำ |
| `qwen3:14b` | 12/12 | 42.4s | 71s | เก็บไว้สำหรับงานยาก |

ก่อนมี RAG: 9/12 สองข้อที่ตกคือคำถามเกี่ยวกับโปรเจกต์ที่โมเดลไม่มีทางรู้เอง

### eval reviewer 11 ข้อ (โค้ดที่ฝังบั๊กไว้ 10 ข้อ + โค้ดสะอาด 1 ข้อ)

| โมเดล | ผ่าน | เฉลี่ยต่อข้อ | ข้อที่พลาด |
|---|---|---|---|
| `qwen2.5:7b` | 9/11 | 11.0s | bare except ที่กลืน error, API key ฝังในโค้ด |
| `qwen3:14b` | 10/11 | 20.8s | โค้ดสะอาด: ยังรายงานประเด็นเล็กน้อยแทนที่จะบอกว่าผ่าน |

สองข้อที่ 7b พลาดเป็นเรื่องที่ reviewer ต้องจับได้ จึงตั้งให้ profile reviewer ใช้ `qwen3:14b`
ส่วน 14b มีแนวโน้ม false positive กับโค้ดที่ไม่มีปัญหา ต้องอ่านผลด้วยวิจารณญาณ ใช้เป็นด่านแรกก่อนคนอ่าน ไม่ใช่ด่านตัดสิน

## Log การใช้งาน

`morty/log.py` บันทึกทุกคู่ถามตอบจากแชทและ review ลง `logs/YYYY-MM-DD.jsonl` แบบ append อย่างเดียว (ปิดด้วย `MORTY_LOG=0`)
eval ไม่ถูกบันทึกเพื่อไม่ให้ปน

```json
{"type": "exchange", "id": "a1b2c3d4e5f6", "ts": "...", "source": "chat", "profile": "dev", "model": "qwen2.5:7b",
 "question": "...", "answer": "...", "seconds": 12.3, "refs": ["morty-project.md#2"]}
{"type": "rating", "ref": "a1b2c3d4e5f6", "ts": "...", "rating": "bad", "note": "ตอบเป็นอังกฤษ"}
```

| คำสั่ง | ทำอะไร |
|---|---|
| ในแชท `/bad เหตุผล`, `/good` | ให้คะแนนคำตอบล่าสุด |
| `python -m morty.log rate <id> bad "เหตุผล"` | ให้คะแนนย้อนหลัง (id แสดงท้ายผล review) |
| `python -m morty.log show [-n 50] [--profile dev]` | รายการล่าสุด |
| `python -m morty.log bad` | เฉพาะที่ติดธง bad พร้อมคำถามและคำตอบ |
| `python -m morty.log to-eval dev` | แปลงเคส bad เป็นบรรทัด eval ให้ไปเติม `expect_any` |
| `python -m morty.log stats` | จำนวนต่อ profile และโมเดล พร้อม good/bad |

log ชุดนี้คือชุดข้อมูลตั้งต้นของ LoRA fine-tune ในอนาคต คู่ที่ติดธง good ใช้เป็นตัวอย่างสอน คู่ที่ติดธง bad ใช้เป็น eval

## คำสั่งในหน้าแชท

| คำสั่ง | ความหมาย |
|---|---|
| `/profile <ชื่อ>` | สลับ profile (ล้างบทสนทนาด้วย) พิมพ์ `/profile` เฉยๆ เพื่อดูรายชื่อ |
| `/model <ชื่อ>` | สลับโมเดลกลางคัน |
| `/bad [เหตุผล]`, `/good` | ติดธงคำตอบล่าสุดลง log |
| `/reset` | ล้างบทสนทนา |
| `/quit` | ออก |

## เรียกจาก Python

```python
from morty import llm

llm.ask("สรุปข้อความนี้ให้หน่อย ...")                        # profile general
llm.ask("อ่านไฟล์ JSON ใน Python ยังไง", profile="dev")      # system prompt + RAG ของ dev
llm.ask("...", profile="dev", rag=False, model="qwen3:14b")  # ทับค่าได้ทุกอย่าง
llm.chat([{"role": "user", "content": "สวัสดี"}])          # ส่งบทสนทนาเต็ม
llm.embed(["ข้อความ 1", "ข้อความ 2"])                      # เวกเตอร์สำหรับค้นหา
```
