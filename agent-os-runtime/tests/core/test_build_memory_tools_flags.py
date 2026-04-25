from pathlib import Path

from agent_os.agent.tools import build_memory_tools
from agent_os.knowledge.asset_store import NullAssetStore
from agent_os.memory.controller import MemoryController


def _names(tools: list[object]) -> set[str]:
    return {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}


def _tool_by_name(tools: list[object], name: str):
    return next(
        t for t in tools if (getattr(t, "name", None) or getattr(t, "__name__", "")) == name
    )


def test_disable_mem0_learning_hides_record_tools(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        enable_mem0_learning=False,
    )
    names = _names(tools)
    assert "record_client_fact" not in names
    assert "record_client_preference" not in names
    assert "search_client_memory" in names  # 读取仍在


def test_disable_hindsight_hides_feedback_tools(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
        enable_hindsight=False,
    )
    tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        enable_hindsight=False,
    )
    names = _names(tools)
    assert "record_task_feedback" not in names
    assert "search_past_lessons" not in names
    assert "retrieve_ordered_context" in names


def test_enable_asset_store_adds_search_tool(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        asset_store=NullAssetStore(),
        enable_asset_store=True,
    )
    names = _names(tools)
    assert "search_reference_cases" in names


def test_search_reference_cases_non_numeric_limit_uses_default(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        asset_store=NullAssetStore(),
        enable_asset_store=True,
    )
    search_cases = _tool_by_name(tools, "search_reference_cases")

    assert search_cases.entrypoint("query", limit="bad") == "（无）"


def test_policy_rejection_is_not_reported_as_duplicate(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    tools = build_memory_tools(ctrl, "c1", None)
    record = _tool_by_name(tools, "record_client_fact")

    out = record.entrypoint("哈哈我开玩笑的，暂时随便说说")

    assert str(out).startswith("policy_rejected:")


def test_record_tools_reject_empty_text(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    tools = build_memory_tools(ctrl, "c1", None)

    assert _tool_by_name(tools, "record_client_fact").entrypoint("   ") == "rejected: empty_text"
    assert _tool_by_name(tools, "record_client_preference").entrypoint("") == "rejected: empty_text"
    assert _tool_by_name(tools, "record_task_feedback").entrypoint("\t") == "rejected: empty_text"
