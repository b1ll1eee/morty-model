"""หน้าจอแชทแบบ TUI (คล้าย Claude Code) ด้วย Textual

เปิดด้วย `morty` หรือ `morty ui [--profile dev] [--workspace .]`
- transcript เลื่อนดูย้อนหลัง แสดง Markdown และโค้ดมีสี
- คำตอบไหลทีละคำเมื่อไม่ใช้เครื่องมือ และแสดงบรรทัด 🔧 เมื่อโมเดลเรียกเครื่องมือ
- กล่องขออนุญาตพร้อม diff ก่อนแก้ไฟล์หรือรันคำสั่ง ตอบ y/n
- พิมพ์ / เพื่อเห็นคำสั่ง: /profile /model /reset /bad /good /quit
- Ctrl+C หยุดคำตอบที่กำลังพิมพ์ (ถ้าไม่มีอะไรรันอยู่ = ออก)  Ctrl+Q ออก

งานกับโมเดลรันใน thread แยก หน้าจอจึงไม่ค้าง การขออนุญาตจาก thread นั้นรอคำตอบผ่าน threading.Event
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Input, Markdown, Static

from . import agent, config, llm, log, profiles, rag

SLASH = ["/profile ", "/model ", "/reset", "/bad ", "/good", "/quit", "/help"]


class PermissionScreen(ModalScreen[bool]):
    """กล่องขออนุญาต แสดงคำอธิบาย (มักเป็น diff) แล้วรอ y/n"""

    BINDINGS = [Binding("y", "allow", "อนุญาต"), Binding("n", "deny", "ปฏิเสธ"), Binding("escape", "deny", "ปฏิเสธ")]
    DEFAULT_CSS = """
    PermissionScreen { align: center middle; }
    #box { width: 90%; max-width: 110; height: auto; max-height: 85%; border: thick $warning; background: $surface; padding: 1 2; }
    #title { text-style: bold; color: $warning; }
    #body { height: auto; max-height: 30; overflow-y: auto; margin: 1 0; }
    #buttons { height: 3; align: center middle; }
    Button { margin: 0 2; }
    """

    def __init__(self, description: str) -> None:
        super().__init__()
        first, _, rest = description.partition("\n")
        self.title_text = first
        self.body_text = rest

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(f"Morty ขอ {self.title_text}", id="title")
            body = self.body_text.strip()
            if body.startswith(("--- ", "diff ")) or "\n@@" in body:
                yield Static(Syntax(body, "diff", theme="ansi_dark", word_wrap=True), id="body")
            else:
                yield Static(body or "(ไม่มีรายละเอียดเพิ่ม)", id="body")
            with Horizontal(id="buttons"):
                yield Button("อนุญาต (y)", id="allow", variant="success")
                yield Button("ปฏิเสธ (n)", id="deny", variant="error")

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "allow")


class MortyApp(App):
    TITLE = "Morty"
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $primary-background; color: $text; padding: 0 1; }
    #transcript { height: 1fr; padding: 0 1; }
    .user { color: $accent; text-style: bold; margin-top: 1; }
    .tool { color: $text-muted; }
    .assistant-label { color: $success; text-style: bold; }
    .answer { margin-bottom: 1; }
    .system { color: $text-muted; text-style: italic; }
    .error { color: $error; }
    #input { dock: bottom; }
    #hint { height: 1; color: $text-muted; padding: 0 1; }
    """
    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "หยุด/ออก", priority=True),
        Binding("ctrl+q", "quit", "ออก", priority=True),
    ]

    def __init__(self, profile_name: str, workspace: Path, model: str | None = None,
                 use_tools: bool = True, auto_yes: bool = False) -> None:
        super().__init__()
        self.profile = profiles.load(profile_name)
        self.model_override = model
        self.model = model or self.profile.model or config.MODEL
        self.options = dict(self.profile.options)
        self.workspace = workspace.resolve()
        self.use_tools = use_tools
        self.auto_yes = auto_yes
        self.history: list[dict] = [{"role": "system", "content": self.profile.system}]
        self.last_id: str | None = None
        self.busy = False
        self.cancel_flag = threading.Event()
        self.last_seconds = 0.0
        self.last_tools = 0

    # ---------- โครงหน้าจอ ----------

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield VerticalScroll(id="transcript")
        yield Static("Enter ส่ง · / คำสั่ง · Ctrl+C หยุด · Ctrl+Q ออก", id="hint")
        yield Input(placeholder="พิมพ์ข้อความ หรือ / เพื่อดูคำสั่ง", id="input",
                    suggester=SuggestFromList(SLASH, case_sensitive=False))

    def on_mount(self) -> None:
        self.refresh_status()
        self.add_system(f"{self.profile.title} พร้อมแล้ว workspace: {self.workspace}  พิมพ์ /help ดูคำสั่ง")
        self.query_one("#input", Input).focus()

    def tools_on(self) -> bool:
        return self.use_tools and bool(self.profile.tools.get("enabled"))

    def refresh_status(self) -> None:
        parts = [
            f"[b]{self.profile.title}[/b]", f"profile {self.profile.name}", f"โมเดล {self.model}",
            "RAG เปิด" if rag.available(self.profile) else "RAG ปิด",
            "เครื่องมือเปิด" if self.tools_on() else "เครื่องมือปิด",
        ]
        if self.last_seconds:
            parts.append(f"ล่าสุด {self.last_seconds:.1f}s" + (f" · {self.last_tools} เครื่องมือ" if self.last_tools else ""))
        if self.busy:
            parts.append("[yellow]กำลังคิด...[/yellow]")
        self.query_one("#status", Static).update(" · ".join(parts))

    # ---------- transcript ----------

    def _add(self, widget) -> None:
        box = self.query_one("#transcript", VerticalScroll)
        box.mount(widget)
        box.scroll_end(animate=False)

    def add_user(self, text: str) -> None:
        self._add(Static(Text(f"คุณ> {text}"), classes="user"))

    def add_system(self, text: str) -> None:
        self._add(Static(text, classes="system"))

    def add_error(self, text: str) -> None:
        self._add(Static(text, classes="error"))

    def add_tool(self, text: str) -> None:
        self._add(Static(Text(f"🔧 {text}"), classes="tool"))

    def start_answer(self) -> Static:
        self._add(Static(f"{self.profile.title}>", classes="assistant-label"))
        widget = Static("", classes="answer")
        self._add(widget)
        return widget

    def finish_answer(self, widget: Static, text: str) -> None:
        """แทนข้อความดิบด้วย Markdown ที่เรนเดอร์แล้ว"""
        box = self.query_one("#transcript", VerticalScroll)
        md = Markdown(text or "(ว่าง)", classes="answer")
        box.mount(md, after=widget)
        widget.remove()
        box.scroll_end(animate=False)

    # ---------- อินพุต ----------

    @on(Input.Submitted, "#input")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self.handle_command(text)
            return
        if self.busy:
            self.add_system("ยังตอบข้อก่อนไม่เสร็จ รอสักครู่หรือกด Ctrl+C เพื่อหยุด")
            return
        self.add_user(text)
        self.history.append({"role": "user", "content": text})
        self.busy = True
        self.cancel_flag.clear()
        self.refresh_status()
        self.generate(text)

    def handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if cmd in ("/quit", "/exit"):
            self.exit()
        elif cmd == "/help":
            self.add_system("/profile <ชื่อ> สลับ expert (ล้างบทสนทนา) · /model <ชื่อ> สลับโมเดล · /reset ล้างบทสนทนา · "
                            "/bad [เหตุผล] และ /good ให้คะแนนคำตอบล่าสุด · /quit ออก")
        elif cmd == "/reset":
            self.history = self.history[:1]
            self.add_system("ล้างบทสนทนาแล้ว")
        elif cmd == "/model":
            if not arg:
                self.add_system(f"โมเดลตอนนี้ {self.model}")
                return
            self.model = arg.strip()
            self.add_system(f"สลับไปใช้ {self.model}")
            self.refresh_status()
        elif cmd == "/profile":
            if not arg:
                self.add_system("profile ที่มี: " + ", ".join(profiles.names()))
                return
            try:
                self.profile = profiles.load(arg.strip())
            except KeyError as e:
                self.add_error(str(e))
                return
            self.model = self.model_override or self.profile.model or config.MODEL
            self.options = dict(self.profile.options)
            self.history = [{"role": "system", "content": self.profile.system}]
            self.add_system(f"สลับเป็น {self.profile.title} แล้ว (ล้างบทสนทนา)")
            self.refresh_status()
        elif cmd in ("/bad", "/good"):
            if not self.last_id:
                self.add_system("ยังไม่มีคำตอบให้ให้คะแนน")
                return
            log.rate(self.last_id, cmd[1:], arg)
            self.add_system(f"บันทึกแล้วว่าคำตอบล่าสุด {cmd[1:]}")
        else:
            self.add_error(f"ไม่รู้จักคำสั่ง {cmd} พิมพ์ /help")

    def action_cancel_or_quit(self) -> None:
        if self.busy:
            self.cancel_flag.set()
            self.add_system("กำลังหยุด... (เครื่องมือที่รันค้างจะเสร็จก่อน)")
        else:
            self.exit()

    # ---------- การขออนุญาต (เรียกจาก thread) ----------

    def confirm_from_thread(self, description: str) -> bool:
        if self.auto_yes:
            self.call_from_thread(self.add_system, f"อนุญาตอัตโนมัติ: {description.splitlines()[0]}")
            return True
        done = threading.Event()
        result: dict[str, bool] = {"ok": False}

        def ask() -> None:
            def on_result(value: bool | None) -> None:
                result["ok"] = bool(value)
                done.set()

            self.push_screen(PermissionScreen(description), on_result)

        self.call_from_thread(ask)
        done.wait()
        self.call_from_thread(self.add_system, ("อนุญาต: " if result["ok"] else "ปฏิเสธ: ") + description.splitlines()[0])
        return result["ok"]

    # ---------- สร้างคำตอบ (thread) ----------

    @work(thread=True, exclusive=True)
    def generate(self, user_text: str) -> None:
        t0 = time.perf_counter()
        tools_used: list[str] = []
        answer = ""
        try:
            augmented, hits = rag.augment(self.profile, user_text)
            outgoing = self.history[:-1] + [{"role": "user", "content": augmented}]
            if hits:
                refs = ", ".join(f"{h.path}#{h.chunk + 1} ({h.score:.2f})" for h in hits)
                self.call_from_thread(self.add_system, f"อ้างอิง: {refs}")
            if self.tools_on():
                ws = agent.make_workspace(self.workspace, self.profile, self.confirm_from_thread)

                def on_event(kind: str, text: str) -> None:
                    if kind == "tool":
                        tools_used.append(text.split("(")[0])
                        self.call_from_thread(self.add_tool, text)

                answer, _ = agent.run(outgoing, model=self.model, options=self.options, ws=ws, on_event=on_event)
                widget = self.call_from_thread(self.start_answer)
                self.call_from_thread(self.finish_answer, widget, answer)
            else:
                widget = self.call_from_thread(self.start_answer)
                parts: list[str] = []
                for piece in llm.chat_stream(outgoing, model=self.model, **self.options):
                    if self.cancel_flag.is_set():
                        parts.append("\n\n(หยุดโดยผู้ใช้)")
                        break
                    parts.append(piece)
                    self.call_from_thread(widget.update, "".join(parts))
                answer = "".join(parts)
                self.call_from_thread(self.finish_answer, widget, answer)
            self.history.append({"role": "assistant", "content": answer})
            self.last_seconds = time.perf_counter() - t0
            self.last_tools = len(tools_used)
            self.last_id = log.record(
                source="chat", profile=self.profile.name, model=self.model, question=user_text, answer=answer,
                seconds=self.last_seconds, refs=[f"{h.path}#{h.chunk + 1}" for h in hits], tools=tools_used,
            )
        except Exception as e:  # noqa: BLE001
            self.history.pop()
            self.call_from_thread(self.add_error, f"เกิดข้อผิดพลาด: {e}")
        finally:
            self.busy = False
            self.call_from_thread(self.refresh_status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Morty แบบหน้าจอ TUI")
    parser.add_argument("--profile", default=profiles.DEFAULT if profiles.DEFAULT in profiles.names() else "dev",
                        choices=profiles.names())
    parser.add_argument("--model", default=None)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--yes", action="store_true", help="อนุญาตอัตโนมัติ (ระวัง)")
    args = parser.parse_args()
    MortyApp(args.profile, Path(args.workspace), model=args.model, use_tools=not args.no_tools, auto_yes=args.yes).run()


if __name__ == "__main__":
    main()
