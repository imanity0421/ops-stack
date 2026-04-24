"""build_memory_tools 的 exclude_tool_names 行为。"""

from pathlib import Path

from ops_agent.agent.tools import build_memory_tools
from ops_agent.memory.controller import MemoryController


def test_exclude_removes_record_tools(tmp_path: Path) -> None:
    local, hind = tmp_path / "m.json", tmp_path / "h.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        golden_rules=None,
        exclude_tool_names={
            "record_client_fact",
            "record_client_preference",
            "record_task_feedback",
        },
    )
    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}
    assert "record_client_fact" not in names
    assert "record_client_preference" not in names
    assert "record_task_feedback" not in names
    assert "retrieve_ordered_context" in names
