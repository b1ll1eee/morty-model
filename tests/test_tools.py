from pathlib import Path

import pytest

from morty import tools


def ws(root: Path, confirm=lambda d: False) -> tools.Workspace:
    return tools.Workspace(root=root.resolve(), confirm=confirm)


def test_read_file_numbers_lines(workspace):
    out = tools.read_file(ws(workspace), "pkg/a.py")
    assert out.splitlines()[0] == "1 | def hello():"
    assert "4 | VALUE = 42" in out


def test_read_file_range(workspace):
    out = tools.read_file(ws(workspace), "pkg/a.py", start_line=4, end_line=4)
    assert out.strip() == "4 | VALUE = 42"


def test_read_file_missing(workspace):
    assert "ไม่พบไฟล์" in tools.read_file(ws(workspace), "nope.py")


def test_path_outside_workspace_is_rejected(workspace):
    assert "นอก workspace" in tools.call(ws(workspace), "read_file", {"path": "../../etc/passwd"})
    with pytest.raises(PermissionError):
        ws(workspace).resolve("/etc/passwd")


def test_list_dir_marks_dirs(workspace):
    out = tools.list_dir(ws(workspace), ".")
    assert "pkg/" in out and "README.md" in out


def test_search_code_reports_file_and_line(workspace):
    out = tools.search_code(ws(workspace), r"VALUE\s*=", glob="*.py")
    assert out.startswith("pkg/a.py:4:")


def test_search_code_bad_regex(workspace):
    assert "regex ไม่ถูกต้อง" in tools.search_code(ws(workspace), "(")


def test_git_rejects_write_commands(workspace):
    assert "อ่านอย่างเดียว" in tools.git(ws(workspace), "push origin main")
    assert "อ่านอย่างเดียว" in tools.git(ws(workspace), "")


def test_run_command_requires_confirmation(workspace):
    denied = tools.run_command(ws(workspace), "echo hi")
    assert "ไม่อนุญาต" in denied
    allowed = tools.run_command(ws(workspace, confirm=lambda d: True), "echo hi")
    assert "exit code 0" in allowed and "hi" in allowed


def test_run_python_output(workspace):
    out = tools.run_python(ws(workspace, confirm=lambda d: True), "print(6*7)")
    assert "42" in out


def test_call_unknown_tool(workspace):
    assert "ไม่มีเครื่องมือ" in tools.call(ws(workspace), "rm_rf", {})


def test_call_respects_allow_list(workspace):
    w = tools.Workspace(root=workspace.resolve(), allow={"read_file"})
    assert "ไม่มีเครื่องมือ" in tools.call(w, "run_command", {"command": "echo"})


def test_output_is_clipped():
    assert tools._clip("x" * 20_000).endswith("ตัวอักษร)")


def test_schemas_match_tools():
    assert set(tools.SCHEMAS) == set(tools.ALL_TOOLS)
    assert [s["function"]["name"] for s in tools.schemas_for(["git", "read_file"])] == ["git", "read_file"]
