from pathlib import Path

from morty import agent, profiles


def test_agent_runs_tool_then_answers(workspace, fake_chat):
    queue, calls = fake_chat
    queue.append({"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "read_file", "arguments": {"path": "pkg/a.py"}}}]})
    queue.append("ไฟล์มีค่า VALUE = 42")
    prof = profiles.load("dev")
    ws = agent.make_workspace(workspace, prof, confirm=lambda d: False)
    events = []
    answer, added = agent.run([{"role": "user", "content": "a.py มีอะไร"}], model="m", options={},
                              ws=ws, on_event=lambda k, t: events.append((k, t)))
    assert answer == "ไฟล์มีค่า VALUE = 42"
    assert added[1]["role"] == "tool" and "VALUE = 42" in added[1]["content"]
    assert events[0][0] == "tool" and events[0][1].startswith("read_file(")
    assert calls[0]["tools"] and calls[1]["messages"][-1]["role"] == "tool"


def test_agent_stops_after_max_rounds(workspace, fake_chat):
    queue, calls = fake_chat
    for _ in range(agent.MAX_ROUNDS):
        queue.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "list_dir", "arguments": {"path": "."}}}]})
    queue.append("สรุปจากที่มี")
    prof = profiles.load("dev")
    ws = agent.make_workspace(workspace, prof, confirm=lambda d: False)
    answer, _ = agent.run([{"role": "user", "content": "วนไป"}], model="m", options={}, ws=ws)
    assert answer == "สรุปจากที่มี"
    assert "ไม่ต้องใช้เครื่องมือเพิ่ม" in calls[-1]["messages"][-1]["content"]


def test_workspace_allow_list_from_profile(workspace):
    prof = profiles.load("dev")
    ws = agent.make_workspace(Path(workspace), prof, confirm=lambda d: False)
    assert "read_file" in ws.allow and ws.root == workspace.resolve()
