"""ตัวอย่างเรียกใช้โมเดลจากโค้ด Python ธรรมดา

รัน:  .venv/bin/python examples_ask.py
"""
from morty import llm

print("โมเดลที่มีในเครื่อง:", llm.list_models())
print()
print(llm.ask("อธิบายว่า LLM คืออะไร ใน 3 ประโยค"))
print()
print("--- profile dev ---")
print(llm.ask("อ่านไฟล์ JSON ใน Python ทำยังไง ตอบสั้นๆ", profile="dev"))
