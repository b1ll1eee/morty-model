# morty-model

โครงโปรเจกต์ LLM ที่รันบนเครื่องตัวเองด้วย [Ollama](https://ollama.com) ฟรีทั้งหมด ข้อมูลไม่ออกนอกเครื่อง
เขียนด้วย Python ออกแบบให้ย้ายไปใช้ Claude API ได้ภายหลังโดยแก้ไฟล์เดียว

## ความต้องการของเครื่อง

- macOS บน Apple Silicon (ทดสอบบน MacBook Pro M4 RAM 24 GB)
- Python 3.12 ขึ้นไป
- พื้นที่ว่างประมาณ 15 GB สำหรับโมเดล

## ติดตั้งครั้งแรก

```bash
# 1) ติดตั้ง Ollama (ถ้ามี Homebrew ใช้ brew install ollama ได้เลย)
mkdir -p ~/.local/ollama ~/.local/bin
curl -fsSL -o /tmp/ollama.tgz https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz
tar -xzf /tmp/ollama.tgz -C ~/.local/ollama
ln -sf ~/.local/ollama/ollama ~/.local/bin/ollama
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# 2) เปิด server แล้วดึงโมเดล
ollama serve &
ollama pull qwen2.5:7b          # โมเดลแชท ขนาดเล็ก ตอบเร็ว
ollama pull qwen3:14b           # โมเดลแชท ฉลาดกว่า ใช้ RAM ราว 10 GB
ollama pull nomic-embed-text    # โมเดล embedding สำหรับทำ RAG

# 3) ติดตั้ง Python dependencies
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## ใช้งาน

```bash
# แชทในเทอร์มินัล (จำบทสนทนาได้ พิมพ์ /quit เพื่อออก)
.venv/bin/python -m morty.chat
.venv/bin/python -m morty.chat --model qwen3:14b

# เรียกจากโค้ด
.venv/bin/python examples_ask.py
```

คำสั่งในหน้าแชท

| คำสั่ง | ความหมาย |
|---|---|
| `/reset` | ล้างบทสนทนา เริ่มใหม่ |
| `/model <ชื่อ>` | สลับโมเดลกลางคัน |
| `/quit` | ออก |

เรียกจาก Python

```python
from morty import llm

llm.ask("สรุปข้อความนี้ให้หน่อย ...")                 # ถามตอบครั้งเดียว
llm.chat([{"role": "user", "content": "สวัสดี"}])   # ส่งบทสนทนาเต็ม
llm.embed(["ข้อความ 1", "ข้อความ 2"])               # แปลงเป็นเวกเตอร์
```

## โครงสร้างโปรเจกต์

```
morty/
  config.py      อ่านค่าจาก .env (โมเดล, host, system prompt)
  llm.py         chat / chat_stream / ask / embed  จุดเดียวที่ต้องแก้ถ้าจะย้ายไป Claude API
  chat.py        แชท CLI แบบจำบทสนทนา
examples_ask.py  ตัวอย่างเรียกใช้จากโค้ด
data/            เอกสารสำหรับทำ RAG (ยังว่าง)
.env             ค่าตั้งค่าของเครื่องนี้ (ไม่ commit)
.env.example     แม่แบบค่าตั้งค่า
```

## สลับโมเดล

แก้บรรทัด `MORTY_MODEL=` ใน `.env` หรือใส่ `--model` ตอนรัน
ดูรายชื่อโมเดลที่มีด้วย `ollama list` และดึงเพิ่มด้วย `ollama pull <ชื่อ>`

แนวทางเลือกโมเดลบนเครื่อง RAM 24 GB

| โมเดล | RAM ที่ใช้ | เหมาะกับ |
|---|---|---|
| `qwen2.5:7b` | ~5 GB | พัฒนาและทดสอบประจำวัน ตอบเร็ว |
| `qwen3:14b` | ~10 GB | งานที่ต้องการความฉลาดมากขึ้น ภาษาไทยดีกว่า |
| `qwen3:32b` | ~20 GB | ฉลาดสุดที่พอรันได้ แต่ช้าและกิน RAM เกือบหมด |

## ขั้นต่อไปที่แนะนำ

1. สร้างชุดคำถามทดสอบ (eval set) ของงานที่ต้องการจริง สัก 20 ถึง 50 ข้อ เพื่อวัดว่าโมเดลไหนทำได้ดี
2. เพิ่ม RAG: ใส่เอกสารใน `data/` แล้วใช้ `llm.embed()` ค้นหาเนื้อหาที่เกี่ยวข้องมาใส่ใน prompt
3. เมื่อโมเดลในเครื่องไม่ฉลาดพอ สลับ `llm.py` ไปใช้ Claude API โดยไม่ต้องแก้ส่วนอื่น
4. ถ้าต้องการสไตล์การตอบเฉพาะทาง ค่อยพิจารณา fine-tune ด้วย LoRA
