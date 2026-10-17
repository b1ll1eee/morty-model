"""แชทกับโมเดลในเทอร์มินัล แบบจำบทสนทนาได้

ใช้งาน:  .venv/bin/python -m morty.chat
         .venv/bin/python -m morty.chat --model qwen3:14b
คำสั่งในแชท:  /reset ล้างบทสนทนา   /model <ชื่อ> สลับโมเดล   /quit ออก
"""
import argparse
import sys

from rich.console import Console
from rich.markdown import Markdown

from . import config, llm

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="แชทกับ Morty")
    parser.add_argument("--model", default=config.MODEL, help="ชื่อโมเดลใน Ollama")
    parser.add_argument("--system", default=config.SYSTEM_PROMPT, help="system prompt")
    args = parser.parse_args()

    model = args.model
    history: list[llm.Message] = [{"role": "system", "content": args.system}]

    console.print(f"[bold green]Morty[/] กำลังใช้โมเดล [cyan]{model}[/]  (พิมพ์ /quit เพื่อออก)")

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

        history.append({"role": "user", "content": user})
        console.print("[bold green]Morty>[/] ", end="")
        parts: list[str] = []
        try:
            for piece in llm.chat_stream(history, model=model):
                parts.append(piece)
                console.print(piece, end="", highlight=False, markup=False)
        except Exception as e:  # noqa: BLE001
            console.print(f"\n[red]เกิดข้อผิดพลาด:[/] {e}")
            history.pop()
            continue
        console.print()
        history.append({"role": "assistant", "content": "".join(parts)})


if __name__ == "__main__":
    sys.exit(main())
