"""แชทกับโมเดลในเทอร์มินัล แบบจำบทสนทนาได้

ใช้งาน:  .venv/bin/python -m morty.chat
         .venv/bin/python -m morty.chat --profile dev
         .venv/bin/python -m morty.chat --model qwen3:14b
คำสั่งในแชท:  /reset ล้างบทสนทนา   /model <ชื่อ> สลับโมเดล
              /profile <ชื่อ> สลับ profile (ล้างบทสนทนาด้วย)   /quit ออก
              /bad [เหตุผล] ติดธงว่าคำตอบล่าสุดไม่ดี   /good ติดธงว่าดี
ถ้า profile มีดัชนี RAG (python -m morty.rag index <ชื่อ>) จะแนบเอกสารที่เกี่ยวข้องให้อัตโนมัติ
profile ที่เปิด [tools] (เช่น dev) จะอ่านไฟล์ ค้นโค้ด ดู git และรันคำสั่งใน workspace ได้
  workspace คือโฟลเดอร์ที่รันคำสั่งนี้ (เปลี่ยนด้วย --workspace) การรันคำสั่งจะถามอนุญาตก่อนทุกครั้ง
  ปิดเครื่องมือด้วย --no-tools  ข้ามการถามด้วย --yes (ระวัง)
ทุกคู่ถามตอบถูกบันทึกลง logs/ (ปิดด้วย MORTY_LOG=0) ดูด้วย python -m morty.log show
"""
import argparse
import sys
import time

from rich.console import Console

from pathlib import Path

from . import agent, config, llm, log, profiles, rag

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="แชทกับ Morty")
    parser.add_argument(
        "--profile", default=profiles.DEFAULT, choices=profiles.names(),
        help="profile ของผู้ช่วย (ค่าเริ่มต้นจาก MORTY_PROFILE ใน .env)",
    )
    parser.add_argument("--model", default=None, help="ชื่อโมเดลใน Ollama (ทับค่าของ profile)")
    parser.add_argument("--system", default=None, help="system prompt (ทับค่าของ profile)")
    parser.add_argument("--workspace", default=".", help="โฟลเดอร์ที่เครื่องมือเข้าถึงได้ (ค่าเริ่มต้น: โฟลเดอร์ปัจจุบัน)")
    parser.add_argument("--no-tools", action="store_true", help="ปิดเครื่องมือแม้ profile จะเปิดไว้")
    parser.add_argument("--yes", action="store_true", help="อนุญาตให้รันคำสั่งโดยไม่ถาม (ระวัง)")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()

    def confirm(desc: str) -> bool:
        if args.yes:
            console.print(f"[yellow]อนุญาตอัตโนมัติ:[/] {desc}")
            return True
        console.print(f"[yellow]Morty ขอ[/] {desc}")
        try:
            ans = console.input("[yellow]อนุญาตไหม? (y/N)[/] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("y", "yes")

    def tools_on() -> bool:
        return bool(profile.tools.get("enabled")) and not args.no_tools

    profile = profiles.load(args.profile)
    model = args.model or profile.model or config.MODEL
    options = dict(profile.options)
    history: list[llm.Message] = [{"role": "system", "content": args.system or profile.system}]

    def banner() -> None:
        rag_state = "RAG เปิด" if rag.available(profile) else "RAG ปิด"
        tool_state = f"เครื่องมือเปิด @ {workspace}" if tools_on() else "เครื่องมือปิด"
        console.print(
            f"[bold green]{profile.title}[/] profile [magenta]{profile.name}[/] "
            f"โมเดล [cyan]{model}[/] {rag_state} {tool_state}  (พิมพ์ /quit เพื่อออก)"
        )

    banner()
    last_id: str | None = None

    while True:
        try:
            user = console.input("[bold blue]คุณ>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not user:
            continue
        if user in ("/quit", "/exit"):
            break
        if user == "/reset":
            history = history[:1]
            console.print("[dim]ล้างบทสนทนาแล้ว[/]")
            continue
        if user.startswith("/model "):
            model = user.split(maxsplit=1)[1]
            console.print(f"[dim]สลับไปใช้ {model}[/]")
            continue
        if user.startswith(("/bad", "/good")):
            if not last_id:
                console.print("[dim]ยังไม่มีคำตอบให้ให้คะแนน[/]")
                continue
            parts = user.split(maxsplit=1)
            rating = parts[0].lstrip("/")
            log.rate(last_id, rating, parts[1] if len(parts) > 1 else "")
            console.print(f"[dim]บันทึกแล้วว่าคำตอบล่าสุด {rating}[/]")
            continue
        if user.startswith("/profile"):
            parts = user.split(maxsplit=1)
            if len(parts) == 1:
                console.print(f"[dim]profile ที่มี: {', '.join(profiles.names())}[/]")
                continue
            try:
                profile = profiles.load(parts[1])
            except KeyError as e:
                console.print(f"[red]{e}[/]")
                continue
            model = args.model or profile.model or config.MODEL
            options = dict(profile.options)
            history = [{"role": "system", "content": profile.system}]
            banner()
            continue

        history.append({"role": "user", "content": user})
        # แนบเอกสารอ้างอิงเฉพาะข้อความที่ส่งให้โมเดลรอบนี้ ไม่เก็บลง history เพื่อไม่ให้ context บวม
        augmented, hits = rag.augment(profile, user)
        outgoing = history[:-1] + [{"role": "user", "content": augmented}]
        if hits:
            console.print("[dim]อ้างอิง: " + ", ".join(f"{h.path}#{h.chunk + 1} ({h.score:.2f})" for h in hits) + "[/]")
        parts_out: list[str] = []
        t0 = time.perf_counter()
        try:
            if tools_on():
                ws = agent.make_workspace(workspace, profile, confirm)

                def on_event(kind: str, text: str) -> None:
                    if kind == "tool":
                        console.print(f"[dim]🔧 {text}[/]", highlight=False)
                    else:
                        console.print(f"[dim]   → {text}[/]", highlight=False)

                answer, _ = agent.run(outgoing, model=model, options=options, ws=ws, on_event=on_event)
                console.print(f"[bold green]{profile.title}>[/] ", end="")
                console.print(answer, highlight=False, markup=False)
            else:
                console.print(f"[bold green]{profile.title}>[/] ", end="")
                for piece in llm.chat_stream(outgoing, model=model, **options):
                    parts_out.append(piece)
                    console.print(piece, end="", highlight=False, markup=False)
                console.print()
                answer = "".join(parts_out)
        except Exception as e:  # noqa: BLE001
            console.print(f"\n[red]เกิดข้อผิดพลาด:[/] {e}")
            history.pop()
            continue
        history.append({"role": "assistant", "content": answer})
        last_id = log.record(
            source="chat", profile=profile.name, model=model, question=user, answer=answer,
            seconds=time.perf_counter() - t0, refs=[f"{h.path}#{h.chunk + 1}" for h in hits],
        )


if __name__ == "__main__":
    sys.exit(main())
