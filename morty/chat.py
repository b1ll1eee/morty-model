"""แชทกับโมเดลในเทอร์มินัล แบบจำบทสนทนาได้

ใช้งาน:  .venv/bin/python -m morty.chat
         .venv/bin/python -m morty.chat --profile dev
         .venv/bin/python -m morty.chat --model qwen3:14b
คำสั่งในแชท:  /reset ล้างบทสนทนา   /model <ชื่อ> สลับโมเดล
              /profile <ชื่อ> สลับ profile (ล้างบทสนทนาด้วย)   /quit ออก
ถ้า profile มีดัชนี RAG (python -m morty.rag index <ชื่อ>) จะแนบเอกสารที่เกี่ยวข้องให้อัตโนมัติ
"""
import argparse
import sys

from rich.console import Console

from . import config, llm, profiles, rag

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="แชทกับ Morty")
    parser.add_argument(
        "--profile", default=profiles.DEFAULT, choices=profiles.names(),
        help="profile ของผู้ช่วย (ค่าเริ่มต้นจาก MORTY_PROFILE ใน .env)",
    )
    parser.add_argument("--model", default=None, help="ชื่อโมเดลใน Ollama (ทับค่าของ profile)")
    parser.add_argument("--system", default=None, help="system prompt (ทับค่าของ profile)")
    args = parser.parse_args()

    profile = profiles.load(args.profile)
    model = args.model or profile.model or config.MODEL
    options = dict(profile.options)
    history: list[llm.Message] = [{"role": "system", "content": args.system or profile.system}]

    def banner() -> None:
        rag_state = "RAG เปิด" if rag.available(profile) else "RAG ปิด"
        console.print(
            f"[bold green]{profile.title}[/] profile [magenta]{profile.name}[/] "
            f"โมเดล [cyan]{model}[/] {rag_state}  (พิมพ์ /quit เพื่อออก)"
        )

    banner()

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
        console.print(f"[bold green]{profile.title}>[/] ", end="")
        parts_out: list[str] = []
        try:
            for piece in llm.chat_stream(outgoing, model=model, **options):
                parts_out.append(piece)
                console.print(piece, end="", highlight=False, markup=False)
        except Exception as e:  # noqa: BLE001
            console.print(f"\n[red]เกิดข้อผิดพลาด:[/] {e}")
            history.pop()
            continue
        console.print()
        history.append({"role": "assistant", "content": "".join(parts_out)})


if __name__ == "__main__":
    sys.exit(main())
