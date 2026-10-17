"""คำสั่ง `morty` ตัวเดียว ใช้จากโฟลเดอร์ไหนก็ได้หลังติดตั้ง (pip install -e . หรือ pipx install .)

  morty                    หน้าจอ TUI แชทกับ dev ในโฟลเดอร์ปัจจุบัน (= morty ui)
  morty chat [--profile X] แชทแบบบรรทัดธรรมดา
  morty review [...]       ตรวจโค้ด: morty review app.py | git diff | morty review
  morty dash               เปิด dashboard สด
  morty eval dev [...]     รัน eval
  morty rag index dev      ทำดัชนีเอกสาร
  morty log bad            ดู log / ให้คะแนน / แปลงเป็น eval
  morty version
"""
from __future__ import annotations

import sys
from importlib import import_module

COMMANDS = {
    "ui": ("morty.ui", "หน้าจอแชทแบบ TUI (ค่าเริ่มต้นเมื่อพิมพ์ morty เฉยๆ)"),
    "chat": ("morty.chat", "แชทแบบบรรทัดธรรมดา (--profile dev) สำหรับ pipe หรือ script"),
    "review": ("morty.review", "ตรวจโค้ด ไฟล์ หรือ git diff"),
    "dash": ("morty.dashboard", "dashboard สด (--serve --open)"),
    "dashboard": ("morty.dashboard", "เหมือน dash"),
    "eval": ("morty.evaluate", "รัน eval ของ profile"),
    "rag": ("morty.rag", "ทำดัชนี/ค้นเอกสาร"),
    "log": ("morty.log", "ดู log การใช้งาน"),
}
DEFAULT_ARGS = {"ui": ["--profile", "dev"], "chat": ["--profile", "dev"], "dash": ["--serve", "--open"], "dashboard": ["--serve", "--open"]}


def usage() -> str:
    lines = ["ใช้งาน: morty <คำสั่ง> [ตัวเลือก]   (ไม่ใส่คำสั่ง = morty ui)", ""]
    lines += [f"  {name:10s} {desc}" for name, (_, desc) in COMMANDS.items() if name != "dashboard"]
    lines += ["  version    เวอร์ชัน", "", "ดูตัวเลือกของแต่ละคำสั่ง: morty <คำสั่ง> --help"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["ui"]
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        print(usage())
        return 0
    if cmd in ("version", "--version", "-V"):
        from importlib.metadata import PackageNotFoundError, version

        try:
            print("morty", version("morty-model"))
        except PackageNotFoundError:
            print("morty (ยังไม่ได้ติดตั้งเป็นแพ็กเกจ รันจากซอร์ส)")
        return 0
    if cmd not in COMMANDS:
        print(f"ไม่รู้จักคำสั่ง {cmd!r}\n\n{usage()}", file=sys.stderr)
        return 2
    module_name, _ = COMMANDS[cmd]
    # ใส่ค่าเริ่มต้นเฉพาะเมื่อผู้ใช้ไม่ได้ระบุตัวเลือกใดเลย
    if not rest and cmd in DEFAULT_ARGS:
        rest = DEFAULT_ARGS[cmd]
    sys.argv = [f"morty {cmd}", *rest]
    module = import_module(module_name)
    result = module.main()
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
