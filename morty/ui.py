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
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from . import agent, config, llm, log, profiles, rag

SLASH = ["/profile ", "/model ", "/theme ", "/reset", "/bad ", "/good", "/quit", "/help"]
THEMES = ["tokyo-night", "catppuccin-mocha", "nord", "gruvbox", "dracula", "textual-dark", "textual-light"]
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MODEL_ALIASES = {"fast": "qwen2.5", "smart": "qwen3", "เร็ว": "qwen2.5", "ฉลาด": "qwen3"}  # แปลงเป็นคำค้นในชื่อโมเดลที่ติดตั้ง


def installed_models() -> list[tuple[str, str]]:
    """[(ชื่อ, ขนาด)] ของโมเดลแชทที่มีในเครื่อง (ตัดโมเดล embedding ออก) ว่างถ้าต่อ Ollama ไม่ได้"""
    try:
        rows = llm._client.list()["models"]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in rows:
        name = m["model"]
        if "embed" in name:
            continue
        out.append((name, f"{m.get('size', 0) / 1e9:.1f} GB"))
    return sorted(out)


def resolve_model(query: str, names: list[str]) -> str | None:
    """'qwen3:14b' ตรงตัว, '14b' / 'qwen3' ค้นในชื่อ, 'fast'/'smart' เป็นชื่อเล่น คืน None ถ้าไม่ชัดเจน"""
    q = query.strip().lower()
    if not q:
        return None
    if q in names:
        return q
    q = MODEL_ALIASES.get(q, q)
    hits = [n for n in names if q in n.lower()]
    return hits[0] if len(hits) == 1 else None
LOGO = """\
███╗   ███╗ ██████╗ ██████╗ ████████╗██╗   ██╗
████╗ ████║██╔═══██╗██╔══██╗╚══██╔══╝╚██╗ ██╔╝
██╔████╔██║██║   ██║██████╔╝   ██║    ╚████╔╝
██║╚██╔╝██║██║   ██║██╔══██╗   ██║     ╚██╔╝
██║ ╚═╝ ██║╚██████╔╝██║  ██║   ██║      ██║
╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝      ╚═╝"""


def _version() -> str:
    try:
        return version("morty-model")
    except PackageNotFoundError:
        return "dev"


class HistoryInput(Input):
    """ช่องพิมพ์ที่กดลูกศรขึ้น/ลงเพื่อเรียกข้อความที่เคยส่ง (เหมือน shell) และจำข้ามรอบผ่าน log"""

    BINDINGS = [Binding("up", "history_prev", "ก่อนหน้า", show=False), Binding("down", "history_next", "ถัดไป", show=False)]
    MAX_HISTORY = 500

    def __init__(self, *args, history: list[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[str] = list(history or [])[-self.MAX_HISTORY:]
        self.pos = len(self.history)  # ชี้หลังรายการสุดท้าย = กำลังพิมพ์ข้อความใหม่
        self.draft = ""

    def remember(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
            self.history = self.history[-self.MAX_HISTORY:]
        self.pos = len(self.history)
        self.draft = ""

    def _show(self, text: str) -> None:
        self.value = text
        self.cursor_position = len(text)

    def action_history_prev(self) -> None:
        if not self.history or self.pos == 0:
            return
        if self.pos == len(self.history):
            self.draft = self.value
        self.pos -= 1
        self._show(self.history[self.pos])

    def action_history_next(self) -> None:
        if self.pos >= len(self.history):
            return
        self.pos += 1
        self._show(self.history[self.pos] if self.pos < len(self.history) else self.draft)


class PickerScreen(ModalScreen[str | None]):
    """รายการให้เลือกด้วยลูกศร + Enter (Esc ยกเลิก) ใช้กับ /model และ /profile"""

    BINDINGS = [Binding("escape", "cancel", "ยกเลิก")]
    DEFAULT_CSS = """
    PickerScreen { align: center middle; background: $background 60%; }
    #pick { width: 70; height: auto; max-height: 80%; border: round $primary; background: $surface; padding: 1 2; }
    #pick { border-title-color: $primary; border-title-style: bold; border-subtitle-color: $text-muted; }
    #options { height: auto; max-height: 20; border: none; background: $surface; }
    """

    def __init__(self, title: str, items: list[tuple[str, str]], current: str | None) -> None:
        super().__init__()
        self.title_text, self.items, self.current = title, items, current

    def compose(self) -> ComposeResult:
        with Vertical(id="pick") as box:
            box.border_title = self.title_text
            box.border_subtitle = "↑↓ เลือก · Enter ตกลง · Esc ยกเลิก"
            options = []
            for value, desc in self.items:
                mark = "●" if value == self.current else "○"
                label = Text.assemble((f"{mark} ", "bold" if value == self.current else ""), (value, "bold"), ("  " + desc, "dim"))
                options.append(Option(label, id=value))
            yield OptionList(*options, id="options")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ids = [v for v, _ in self.items]
        if self.current in ids:
            ol.highlighted = ids.index(self.current)
        ol.focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PermissionScreen(ModalScreen[bool]):
    """กล่องขออนุญาต แสดงคำอธิบาย (มักเป็น diff) แล้วรอ y/n"""

    BINDINGS = [Binding("y", "allow", "อนุญาต"), Binding("n", "deny", "ปฏิเสธ"), Binding("escape", "deny", "ปฏิเสธ")]
    DEFAULT_CSS = """
    PermissionScreen { align: center middle; background: $background 60%; }
    #box { width: 90%; max-width: 110; height: auto; max-height: 85%; border: round $warning; background: $surface; padding: 1 2; }
    #box { border-title-color: $warning; border-title-style: bold; border-subtitle-color: $text-muted; }
    #title { text-style: bold; color: $text; margin-bottom: 1; }
    #body { height: auto; max-height: 30; overflow-y: auto; margin: 0 0 1 0; padding: 0 1; background: $panel; }
    #buttons { height: 3; align: center middle; }
    Button { margin: 0 2; min-width: 16; }
    """

    def __init__(self, description: str) -> None:
        super().__init__()
        first, _, rest = description.partition("\n")
        self.title_text = first
        self.body_text = rest

    def compose(self) -> ComposeResult:
        icon = "✎" if self.title_text.startswith(("แก้ไฟล์", "เขียนทับ", "สร้างไฟล์")) else "▶"
        with Vertical(id="box") as box:
            box.border_title = f"{icon} ขออนุญาต"
            box.border_subtitle = "y อนุญาต · n ปฏิเสธ · Esc ปฏิเสธ"
            yield Static(self.title_text, id="title")
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
    #status { height: 1; background: $panel; color: $text; padding: 0 1; }
    #transcript { height: 1fr; padding: 0 2 1 2; scrollbar-size: 1 1; }
    #welcome { border: round $primary; padding: 0 2; margin: 1 0; height: auto; background: $surface; }
    #welcome { border-title-color: $primary; border-subtitle-color: $text-muted; }
    .logo { color: $primary; text-style: bold; }
    .user { padding: 0 1; border-left: thick $accent; color: $text; }
    .user-label { color: $accent; text-style: bold; margin-top: 1; }
    .tool { color: $text-muted; padding: 0 0 0 2; }
    .tool-result { color: $text-muted; text-style: dim; padding: 0 0 0 5; }
    .assistant { border: round $success 50%; padding: 0 1; margin: 0 0 1 0; height: auto; background: $surface; }
    .assistant { border-title-color: $success; border-title-style: bold; border-subtitle-color: $text-muted; }
    .answer { height: auto; }
    .system { color: $text-muted; text-style: italic; padding: 0 1; }
    .error { color: $error; padding: 0 1; }
    .thinking { color: $warning; padding: 0 1; }
    #bottom { dock: bottom; height: auto; margin: 0 1 1 1; }
    #input { border: round $primary; background: $surface; padding: 0 1; }
    #input:focus { border: round $accent; }
    #hint { height: 1; color: $text-muted; padding: 0 2; }
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
        self.started_at = 0.0
        self.spinner_i = 0
        self.thinking: Static | None = None

    # ---------- โครงหน้าจอ ----------

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield VerticalScroll(id="transcript")
        with Vertical(id="bottom"):
            yield HistoryInput(placeholder="พิมพ์ข้อความ หรือ / เพื่อดูคำสั่ง  (↑↓ ย้อนข้อความเก่า)", id="input",
                               suggester=SuggestFromList(SLASH, case_sensitive=False), history=self.load_history())
            yield Static("[b]Enter[/b] ส่ง  ·  [b]↑↓[/b] ข้อความเก่า  ·  [b]/[/b] คำสั่ง  ·  [b]Ctrl+C[/b] หยุด  ·  [b]Ctrl+Q[/b] ออก  ·  [b]/theme[/b] ธีม", id="hint")

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self.refresh_status()
        self.add_welcome()
        self.query_one("#input", Input).focus()
        self.set_interval(0.12, self._tick)

    def add_welcome(self) -> None:
        box = Vertical(id="welcome")
        box.border_title = f"Morty v{_version()}"
        box.border_subtitle = "local · private · ollama"
        self._add(box)
        box.mount(Static(LOGO, classes="logo"))
        tips = Text()
        tips.append(self.profile.title, style="bold")
        tips.append(f"  ·  โมเดล {self.model}  ·  workspace ")
        tips.append(str(self.workspace), style="dim")
        tips.append("\n\n")
        tips.append("›  ", style="bold")
        tips.append("ถามเรื่องโค้ดในโฟลเดอร์นี้ได้เลย เช่น  \"ไฟล์ main.py ทำอะไร\"  หรือ  \"แก้ TIMEOUT ใน config.py เป็น 30\"\n")
        tips.append("›  ", style="bold")
        tips.append("ก่อนแก้ไฟล์หรือรันคำสั่ง Morty จะแสดง diff แล้วขออนุญาตเสมอ\n")
        tips.append("›  ", style="bold")
        tips.append("ตอบไม่ดีพิมพ์  /bad เหตุผล  เพื่อเก็บไว้ปรับปรุง   พิมพ์  /help  ดูคำสั่งทั้งหมด")
        box.mount(Static(tips))

    def load_history(self) -> list[str]:
        """ข้อความที่เคยส่งจากแชทครั้งก่อนๆ (จาก logs/) เพื่อให้ลูกศรขึ้นใช้ได้ตั้งแต่เปิดโปรแกรม"""
        try:
            rows = [e["question"] for e in log.exchanges() if e.get("source") == "chat" and "\n" not in e["question"]]
            return rows[-HistoryInput.MAX_HISTORY:]
        except Exception:  # noqa: BLE001
            return []

    def _tick(self) -> None:
        if self.busy and self.thinking is not None:
            self.spinner_i = (self.spinner_i + 1) % len(SPINNER)
            elapsed = time.perf_counter() - self.started_at
            self.thinking.update(f"{SPINNER[self.spinner_i]} กำลังคิด · {elapsed:.1f}s")

    def context_gauge(self) -> str:
        used = sum(len(m.get("content", "")) for m in self.history) // 3  # ประมาณ token อย่างหยาบ
        limit = int(self.options.get("num_ctx", 2048))
        filled = min(8, round(8 * used / limit)) if limit else 0
        return f"{'▰' * filled}{'▱' * (8 - filled)} {used / 1000:.1f}k/{limit // 1000}k"

    def tools_on(self) -> bool:
        return self.use_tools and bool(self.profile.tools.get("enabled"))

    def refresh_status(self) -> None:
        on, off = "[$success]●[/]", "[$text-muted]○[/]"
        parts = [
            f"[b]◆ {self.profile.title}[/b]", f"⬢ {self.model}",
            f"{on if rag.available(self.profile) else off} RAG",
            f"{on if self.tools_on() else off} เครื่องมือ",
            f"▣ {self.context_gauge()}",
        ]
        if self.last_seconds:
            parts.append(f"⏱ {self.last_seconds:.1f}s" + (f" · 🔧 {self.last_tools}" if self.last_tools else ""))
        if self.busy:
            parts.append("[$warning]● กำลังคิด[/]")
        self.query_one("#status", Static).update("  ".join(parts))

    # ---------- transcript ----------

    def _add(self, widget) -> None:
        box = self.query_one("#transcript", VerticalScroll)
        box.mount(widget)
        box.scroll_end(animate=False)

    def add_user(self, text: str) -> None:
        self._add(Static("คุณ ›", classes="user-label"))
        self._add(Static(Text(text), classes="user"))

    def add_system(self, text: str) -> None:
        self._add(Static(text, classes="system"))

    def add_error(self, text: str) -> None:
        self._add(Static(f"✖ {text}", classes="error"))

    def add_tool(self, text: str) -> None:
        name, _, args = text.partition("(")
        self._add(Static(Text.assemble(("▸ ", "bold"), (name, "bold"), (" " + args.rstrip(")"), "dim")), classes="tool"))

    def add_tool_result(self, text: str) -> None:
        if text:
            self._add(Static(Text(f"→ {text}"), classes="tool-result"))

    def start_thinking(self) -> None:
        self.started_at = time.perf_counter()
        self.thinking = Static(f"{SPINNER[0]} กำลังคิด · 0.0s", classes="thinking")
        self._add(self.thinking)

    def stop_thinking(self) -> None:
        if self.thinking is not None:
            self.thinking.remove()
            self.thinking = None

    def start_answer(self) -> Static:
        self.stop_thinking()
        panel = Vertical(classes="assistant")
        panel.border_title = self.profile.title
        self._add(panel)
        widget = Static("▌", classes="answer")
        panel.mount(widget)
        return widget

    def finish_answer(self, widget: Static, text: str) -> None:
        """แทนข้อความดิบด้วย Markdown ที่เรนเดอร์แล้ว และใส่เวลาที่ใช้ไว้มุมกล่อง"""
        panel = widget.parent
        md = Markdown(text or "(ว่าง)", classes="answer")
        panel.mount(md, after=widget)
        widget.remove()
        panel.border_subtitle = f"{time.perf_counter() - self.started_at:.1f}s"
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    # ---------- อินพุต ----------

    @on(Input.Submitted, "#input")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if isinstance(event.input, HistoryInput):
            event.input.remember(text)
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
        self.start_thinking()
        self.refresh_status()
        self.generate(text)

    def handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if cmd in ("/quit", "/exit"):
            self.exit()
        elif cmd == "/help":
            self.add_system("/model เปิดรายการโมเดล หรือ /model 14b, fast, smart · /profile เปิดรายการ expert (ล้างบทสนทนา) · /theme <ชื่อ> เปลี่ยนธีม · "
                            "/reset ล้างบทสนทนา · /bad [เหตุผล] และ /good ให้คะแนนคำตอบล่าสุด · /quit ออก")
        elif cmd == "/theme":
            if arg.strip() in THEMES:
                self.theme = arg.strip()
                self.add_system(f"ธีม {self.theme}")
            else:
                self.add_system("ธีมที่มี: " + ", ".join(THEMES))
        elif cmd == "/reset":
            self.history = self.history[:1]
            self.add_system("ล้างบทสนทนาแล้ว")
        elif cmd == "/model":
            models = installed_models()
            names = [n for n, _ in models]
            if not arg:
                if not models:
                    self.add_system(f"โมเดลตอนนี้ {self.model} (ต่อ Ollama ไม่ได้ จึงแสดงรายการไม่ได้)")
                    return
                self.push_screen(PickerScreen("เลือกโมเดล", models, self.model), self.set_model)
                return
            chosen = resolve_model(arg, names) or (arg.strip() if not names else None)
            if chosen is None:
                self.add_error(f"ไม่พบโมเดลที่ตรงกับ {arg.strip()!r} มีให้เลือก: " + ", ".join(names) + "  (ใช้ชื่อสั้นได้ เช่น 7b, 14b, fast, smart)")
                return
            self.set_model(chosen)
        elif cmd == "/profile":
            if not arg:
                items = []
                for name in profiles.names():
                    pr = profiles.load(name)
                    items.append((name, f"{pr.title} · {pr.model or config.MODEL}"))
                self.push_screen(PickerScreen("เลือก profile", items, self.profile.name), self.set_profile)
                return
            self.set_profile(arg.strip())
        elif cmd in ("/bad", "/good"):
            if not self.last_id:
                self.add_system("ยังไม่มีคำตอบให้ให้คะแนน")
                return
            log.rate(self.last_id, cmd[1:], arg)
            self.add_system(f"บันทึกแล้วว่าคำตอบล่าสุด {cmd[1:]}")
        else:
            self.add_error(f"ไม่รู้จักคำสั่ง {cmd} พิมพ์ /help")

    def set_model(self, name: str | None) -> None:
        if not name:
            return
        self.model = name
        self.add_system(f"สลับไปใช้โมเดล {self.model}")
        self.refresh_status()

    def set_profile(self, name: str | None) -> None:
        if not name:
            return
        try:
            self.profile = profiles.load(name)
        except KeyError as e:
            self.add_error(str(e))
            return
        self.model = self.model_override or self.profile.model or config.MODEL
        self.options = dict(self.profile.options)
        self.history = [{"role": "system", "content": self.profile.system}]
        self.add_system(f"สลับเป็น {self.profile.title} แล้ว (ล้างบทสนทนา)")
        self.refresh_status()

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
                    else:
                        self.call_from_thread(self.add_tool_result, text)

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
                    self.call_from_thread(widget.update, "".join(parts) + "▌")
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
            self.call_from_thread(self.stop_thinking)
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
