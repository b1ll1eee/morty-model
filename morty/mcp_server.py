"""MCP server: ให้ Claude Code (หรือ client MCP อื่น) เรียกใช้โมเดลของ Morty เป็นเครื่องมือ

เครื่องมือที่เปิดให้
  morty_ask          ถาม Morty ด้วย profile ที่เลือก (ค่าเริ่มต้น dev พร้อม RAG) ทำงานในเครื่อง ข้อมูลไม่ออกนอกเครื่อง
  morty_review       ให้ reviewer ตรวจโค้ด (ข้อความโค้ด, ไฟล์ หรือ git diff) ด้วย qwen3:14b + ruff/bandit
  morty_search_docs  ค้นเอกสารใน data/<profile>/ ด้วย embedding คืนท่อนที่เกี่ยวพร้อมคะแนน
  morty_profiles     รายชื่อ profile และโมเดลที่แต่ละตัวใช้

รัน:  .venv/bin/python -m morty.mcp_server          (stdio ค่าเริ่มต้น ใช้กับ .mcp.json ของโปรเจกต์)
ห้าม print ลง stdout ในโมดูลนี้ เพราะ stdout คือช่องทางส่งข้อความ MCP
ทุกการเรียกถูกบันทึกลง logs/ ด้วย source="mcp" จึงเห็นใน dashboard แยกจากแชทปกติ
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import config, llm, log, profiles, rag, reviewpipe

server = MCPServer(
    "morty",
    instructions=(
        "Morty คือ LLM ที่รันในเครื่องนี้ (Ollama) ใช้เมื่อ: ต้องการคำตอบจากเอกสารภายในใน data/dev/ (morty_search_docs, morty_ask), "
        "ต้องการ code review รอบแรกราคาถูกก่อนอ่านเอง (morty_review), หรืองานที่ข้อมูลห้ามออกนอกเครื่อง "
        "โมเดลเล็กกว่า Claude มาก: ใช้ผลเป็นข้อมูลประกอบ ไม่ใช่คำตัดสิน"
    ),
)


def _record(source_tool: str, profile: str, model: str, question: str, answer: str, t0: float, **extra) -> None:
    try:
        log.record(source="mcp", profile=profile, model=model, question=question, answer=answer,
                   seconds=time.perf_counter() - t0, meta={"tool": source_tool, **extra})
    except Exception:  # noqa: BLE001
        pass


@server.tool(description="ถาม Morty (LLM ในเครื่อง) ด้วย profile ที่เลือก: general = ทั่วไป, dev = โค้ดและเอกสารภายในผ่าน RAG, reviewer = มุมมองผู้ตรวจ")
def morty_ask(question: str, profile: str = "dev", model: str | None = None, rag: bool = True) -> str:
    if profile not in profiles.names():
        return f"ไม่มี profile {profile} (มี: {', '.join(profiles.names())})"
    prof = profiles.load(profile)
    used_model = model or prof.model or config.MODEL
    t0 = time.perf_counter()
    answer = llm.ask(question, profile=profile, model=used_model, rag=rag)
    _record("morty_ask", profile, used_model, question, answer, t0)
    return answer


@server.tool(description="ให้ morty-reviewer ตรวจโค้ด: ส่ง code (ข้อความ) หรือ path (ไฟล์ใน repo) หรือ diff=true (git diff ของ repo นี้) ได้ finding ระดับ bug/risk/style พร้อม ruff+bandit")
def morty_review(code: str | None = None, path: str | None = None, diff: bool = False,
                 staged: bool = False, language: str = "python", model: str | None = None) -> str:
    sections: list[tuple[str, str, bool]] = []
    py_files: list[Path] = []
    snippet = None
    if diff:
        cmd = ["git", "diff", "--name-only"] + (["--staged"] if staged else [])
        changed = subprocess.run(cmd, capture_output=True, text=True, cwd=config.ROOT).stdout.split()
        text = subprocess.run(["git", "diff"] + (["--staged"] if staged else []), capture_output=True, text=True, cwd=config.ROOT).stdout
        if not text.strip():
            return "git diff ว่างเปล่า ไม่มีอะไรให้ตรวจ"
        sections.append(("git diff", text, False))
        py_files = [config.ROOT / f for f in changed if f.endswith(".py") and (config.ROOT / f).is_file()]
    elif path:
        p = Path(path)
        if not p.is_absolute():
            p = config.ROOT / p
        if not p.is_file():
            return f"ไม่พบไฟล์: {path}"
        sections.append((str(path), p.read_text(encoding="utf-8", errors="replace"), True))
        if p.suffix == ".py":
            py_files = [p]
    elif code:
        sections.append(("snippet", code, True))
        snippet = (code, language)
    else:
        return "ต้องส่ง code, path หรือ diff=true อย่างใดอย่างหนึ่ง"

    from .review import build_prompt  # import ตรงนี้เพื่อไม่ดึง rich console ตอนโหลดโมดูล

    prompt = build_prompt(sections, focus=None)
    prof = profiles.load("reviewer")
    used_model = model or prof.model or config.MODEL
    t0 = time.perf_counter()
    text, report = reviewpipe.single_review(prompt, py_files=py_files or None, snippet=snippet, model=used_model)
    answer = reviewpipe.render_single(text, report)
    _record("morty_review", "reviewer", used_model, prompt, answer, t0, analyzer=report.analyzer_ran)
    return answer


@server.tool(description="ค้นเอกสารภายในที่ทำดัชนีไว้ใน data/<profile>/ ด้วย embedding คืนท่อนที่เกี่ยวข้องพร้อมคะแนนความคล้าย (0-1)")
def morty_search_docs(query: str, profile: str = "dev", top_k: int = 4) -> str:
    prof = profiles.load(profile)
    if not prof.data_dir or not rag.available(prof):
        return f"profile {profile} ยังไม่มีดัชนี RAG (รัน: python -m morty.rag index {profile})"
    hits = rag.search(prof.data_dir, query, top_k=top_k, min_score=0.0)
    if not hits:
        return "ไม่พบท่อนที่เกี่ยวข้อง"
    return "\n\n".join(f"[{i + 1}] {h.path} ท่อน {h.chunk + 1} (คะแนน {h.score:.2f})\n{h.text}" for i, h in enumerate(hits))


@server.tool(description="รายชื่อ profile ของ Morty พร้อมโมเดลที่ใช้ และสถานะ RAG/เครื่องมือ")
def morty_profiles() -> str:
    rows = []
    for name in profiles.names():
        p = profiles.load(name)
        rows.append(f"{name}: โมเดล {p.model or config.MODEL}, RAG {'เปิด' if rag.available(p) else 'ปิด'}, "
                    f"เครื่องมือ {'เปิด' if p.tools.get('enabled') else 'ปิด'} — {p.title}")
    return "\n".join(rows)


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
