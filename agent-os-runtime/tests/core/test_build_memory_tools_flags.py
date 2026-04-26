from pathlib import Path

from agent_os.agent.tools import build_memory_tools
from agent_os.knowledge.asset_store import AssetSearchHit, NullAssetStore
from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemoryLane, UserFact


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


def test_retrieve_ordered_context_tool_entrypoint_uses_unified_context(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="c1",
            user_id=None,
            scope="client_shared",
            text="公司长期要求：输出必须先给结论",
            fact_type="attribute",
        )
    )
    tools = build_memory_tools(ctrl, "c1", None, enable_hindsight=False)

    out = _tool_by_name(tools, "retrieve_ordered_context").entrypoint("结论")

    assert "## ① 主体画像 (Mem0)" in out
    assert "公司长期要求" in out
    assert "## ② 历史教训与反馈 (Hindsight)" in out
    assert "## ③ 领域知识" in out
    assert "## ④ 参考案例 (Asset Store)" in out


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


def test_hindsight_synthesis_runs_only_when_enabled(tmp_path: Path, monkeypatch) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            user_id="u1",
            skill_id="default_agent",
            text="用户认为脚本开头太慢，下次先抛冲突",
            fact_type="feedback",
        )
    )

    called = {"n": 0}

    def fake_synth(**kwargs):
        called["n"] += 1
        assert kwargs["query"] == "脚本开头"
        assert kwargs["candidates"]
        return "SYNTHESIZED"

    monkeypatch.setattr(
        "agent_os.memory.hindsight_synthesizer.synthesize_hindsight_context",
        fake_synth,
    )

    disabled_tools = build_memory_tools(ctrl, "c1", "u1", skill_id="default_agent")
    assert "SYNTHESIZED" not in _tool_by_name(disabled_tools, "search_past_lessons").entrypoint(
        "脚本开头"
    )
    assert called["n"] == 0

    enabled_tools = build_memory_tools(
        ctrl,
        "c1",
        "u1",
        skill_id="default_agent",
        enable_hindsight_synthesis=True,
    )
    assert (
        _tool_by_name(enabled_tools, "search_past_lessons").entrypoint("脚本开头") == "SYNTHESIZED"
    )
    assert called["n"] == 1


class _HitAssetStore(NullAssetStore):
    def search(self, *args, **kwargs):
        return [
            AssetSearchHit(
                case_id="asset-1",
                scope="system",
                asset_type="style_reference",
                summary="金牌脚本摘要",
                feature_summary="痛点前置",
                style_fingerprint="强钩子、短句",
                style_tags=["痛点前置"],
            )
        ]


def test_asset_synthesis_runs_only_when_enabled(tmp_path: Path, monkeypatch) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    called = {"n": 0}

    def fake_synth(**kwargs):
        called["n"] += 1
        assert kwargs["query"] == "短视频开头"
        assert kwargs["hits"][0].case_id == "asset-1"
        assert kwargs["asset_type"] == "style_reference"
        return "ASSET_SYNTHESIZED"

    monkeypatch.setattr(
        "agent_os.knowledge.asset_synthesizer.synthesize_asset_context",
        fake_synth,
    )

    disabled_tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        asset_store=_HitAssetStore(),
        enable_asset_store=True,
    )
    disabled_out = _tool_by_name(disabled_tools, "search_reference_cases").entrypoint(
        "短视频开头", asset_type="style_reference"
    )
    assert "ASSET_SYNTHESIZED" not in disabled_out
    assert "金牌脚本摘要" in disabled_out
    assert called["n"] == 0

    enabled_tools = build_memory_tools(
        ctrl,
        "c1",
        None,
        asset_store=_HitAssetStore(),
        enable_asset_store=True,
        enable_asset_synthesis=True,
    )
    enabled_out = _tool_by_name(enabled_tools, "search_reference_cases").entrypoint(
        "短视频开头", asset_type="style_reference"
    )
    assert enabled_out == "ASSET_SYNTHESIZED"
    assert called["n"] == 1
