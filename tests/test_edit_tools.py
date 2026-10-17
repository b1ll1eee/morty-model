from pathlib import Path

from morty import tools


def ws(root: Path, allow=True) -> tools.Workspace:
    return tools.Workspace(root=root.resolve(), confirm=lambda d: allow)


def test_edit_file_replaces_unique_match_and_shows_diff(workspace):
    seen = {}
    w = tools.Workspace(root=workspace.resolve(), confirm=lambda d: seen.update(d=d) or True)
    out = tools.edit_file(w, "pkg/a.py", "return 'hi'", "return 'hello'")
    assert out.startswith("แก้ pkg/a.py แล้ว")
    assert (workspace / "pkg/a.py").read_text(encoding="utf-8").count("return 'hello'") == 1
    assert "-    return 'hi'" in seen["d"] and "+    return 'hello'" in seen["d"]


def test_edit_file_requires_unique_and_existing_old(workspace):
    (workspace / "pkg/a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    assert "2 แห่ง" in tools.edit_file(ws(workspace), "pkg/a.py", "x = 1", "x = 2")
    assert "ไม่พบข้อความ old" in tools.edit_file(ws(workspace), "pkg/a.py", "y = 1", "y = 2")
    assert "ไม่พบไฟล์" in tools.edit_file(ws(workspace), "pkg/none.py", "a", "b")


def test_edit_file_denied_leaves_file(workspace):
    before = (workspace / "pkg/a.py").read_text(encoding="utf-8")
    assert "ไม่อนุญาต" in tools.edit_file(ws(workspace, allow=False), "pkg/a.py", "'hi'", "'x'")
    assert (workspace / "pkg/a.py").read_text(encoding="utf-8") == before


def test_write_file_creates_and_overwrites(workspace):
    out = tools.write_file(ws(workspace), "pkg/new/mod.py", "VALUE = 1\n")
    assert out.startswith("สร้างไฟล์ใหม่") and (workspace / "pkg/new/mod.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert tools.write_file(ws(workspace), "pkg/new/mod.py", "VALUE = 1\n").endswith("ไม่ได้เปลี่ยนอะไร")
    assert "ไม่เขียนทับ" in tools.write_file(ws(workspace), "pkg/new/mod.py", "VALUE = 2\n")  # ต้องระบุ overwrite
    assert (workspace / "pkg/new/mod.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert tools.write_file(ws(workspace), "pkg/new/mod.py", "VALUE = 2\n", overwrite=True).startswith("เขียนทับ")
    assert "ไม่อนุญาต" in tools.write_file(ws(workspace, allow=False), "pkg/new/mod.py", "VALUE = 3\n", overwrite=True)


def test_write_file_outside_workspace_rejected(workspace):
    assert "นอก workspace" in tools.call(ws(workspace), "write_file", {"path": "../evil.py", "content": "x"})
