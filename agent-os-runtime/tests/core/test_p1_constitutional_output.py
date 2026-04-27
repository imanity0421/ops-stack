from __future__ import annotations

import json
from pathlib import Path

from agent_os.agent.constitutional import MARKER, build_constitutional_instruction_blocks
from agent_os.agent.factory import get_agent
from agent_os.config import Settings
from agent_os.manifest_loader import AgentManifestV1, load_skill_manifest_registry
from agent_os.manifest_output import PlanStructuredV1, resolve_structured_output_model
from agent_os.memory.controller import MemoryController
from agent_os.agent.task_memory import TaskSegment, TaskSummary


def test_constitutional_marker_in_blocks() -> None:
    blocks = build_constitutional_instruction_blocks(None, enabled=True)
    assert len(blocks) >= 1
    assert MARKER in blocks[0]


def test_constitutional_extra_from_manifest() -> None:
    m = AgentManifestV1(constitutional_prompt="本 skill 须额外强调：不承诺具体 ROI。")
    blocks = build_constitutional_instruction_blocks(m, enabled=True)
    assert any("不承诺具体 ROI" in b for b in blocks)


def test_constitutional_disabled_empty() -> None:
    assert build_constitutional_instruction_blocks(None, enabled=False) == []


def test_plan_structured_v1_roundtrip_five_times() -> None:
    for i in range(5):
        m = PlanStructuredV1(
            title=f"标题{i}",
            outline=[f"点A{i}", f"点B{i}"],
            key_messages=[f"k{i}"],
            body_markdown=f"正文{i}" * 20,
        )
        raw = json.dumps(m.model_dump(), ensure_ascii=False)
        PlanStructuredV1.model_validate_json(raw)


def test_plan_structured_v1_coerces_empty_outline() -> None:
    m = PlanStructuredV1.model_validate({"title": "标题", "outline": None, "body_markdown": "正文"})
    assert m.outline == ["（未生成提纲）"]


def test_resolve_structured_v1_from_manifest() -> None:
    m = AgentManifestV1(
        output_mode="structured_v1",
        output_schema_version="1.0",
    )
    assert resolve_structured_output_model(m) is PlanStructuredV1
    assert resolve_structured_output_model(AgentManifestV1()) is None


def test_get_agent_planning_draft_has_output_schema(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_constitutional_prompt=True,
        session_sqlite_path=tmp_path / "s.db",
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        skill_id="planning_draft",
    )
    assert ag.output_schema is not None
    assert getattr(ag, "structured_outputs", None) is True
    inst = ag.instructions
    assert inst is not None
    if isinstance(inst, list):
        flat = "\n".join(str(x) for x in inst)
    else:
        flat = str(inst)
    assert MARKER in flat
    assert "【运行时临时上下文】" not in flat
    assert "入口：api" not in flat

    tool_names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in ag.tools}
    assert tool_names == {"retrieve_ordered_context"}


def test_packaged_planning_draft_manifest_loads() -> None:
    reg = load_skill_manifest_registry()
    m = reg.get("planning_draft")
    assert m is not None
    assert m.output_mode == "structured_v1"
    assert resolve_structured_output_model(m) is PlanStructuredV1


def test_get_agent_respects_disable_constitutional(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_constitutional_prompt=False,
        session_sqlite_path=tmp_path / "s.db",
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        skill_id="default_agent",
    )
    inst = ag.instructions
    if isinstance(inst, list):
        flat = "\n".join(str(x) for x in inst)
    else:
        flat = str(inst)
    assert MARKER not in flat


def test_get_agent_static_prefix_omits_handoff_status_with_context_builder(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    handoff = tmp_path / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "handoff_version": "1.0",
                "created_utc": "2026-04-27T00:00:00+00:00",
                "video_raw_ingest_schema_ref": "/schema.json",
                "lessons": [{"valid": True}],
            }
        ),
        encoding="utf-8",
    )
    s = Settings(
        session_sqlite_path=tmp_path / "s.db",
        handoff_manifest_path=handoff,
    )

    ag1 = get_agent(ctrl, client_id="c1", settings=s)
    ag2 = get_agent(ctrl, client_id="c1", settings=s)
    flat1 = "\n".join(str(x) for x in ag1.instructions)
    flat2 = "\n".join(str(x) for x in ag2.instructions)

    assert flat1 == flat2
    assert "handoff_version=1.0" not in flat1
    assert "条目数=" not in flat1
    assert "2026-04-27" not in flat1
    assert "当前配方手册版本" not in flat1


def test_get_agent_legacy_mode_can_include_handoff_status(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    handoff = tmp_path / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "handoff_version": "1.0",
                "created_utc": "2026-04-27T00:00:00+00:00",
                "video_raw_ingest_schema_ref": "/schema.json",
                "lessons": [{"valid": True}],
            }
        ),
        encoding="utf-8",
    )
    s = Settings(
        session_sqlite_path=tmp_path / "s.db",
        handoff_manifest_path=handoff,
        enable_context_builder=False,
        enable_ephemeral_metadata=False,
    )

    ag = get_agent(ctrl, client_id="c1", settings=s)
    flat = "\n".join(str(x) for x in ag.instructions)

    assert "handoff_version=1.0" in flat
    assert "当前配方手册版本" in flat
    assert "2026-04-27" not in flat


def test_get_agent_default_manifest_uses_explicit_minimal_tools(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(session_sqlite_path=tmp_path / "s.db")
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        skill_id="default_agent",
    )
    tool_names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in ag.tools}

    assert tool_names == {
        "retrieve_ordered_context",
        "record_client_fact",
        "record_client_preference",
        "record_task_feedback",
    }


def test_get_agent_tool_mask_is_explicit_per_entrypoint_scope(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(session_sqlite_path=tmp_path / "s.db")
    cli_agent = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        skill_id="default_agent",
        entrypoint="cli",
    )
    web_agent = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        skill_id="default_agent",
        entrypoint="web",
        exclude_tool_names={
            "record_client_fact",
            "record_client_preference",
            "record_task_feedback",
        },
    )
    cli_tools = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in cli_agent.tools}
    web_tools = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in web_agent.tools}

    assert cli_tools == {
        "retrieve_ordered_context",
        "record_client_fact",
        "record_client_preference",
        "record_task_feedback",
    }
    assert web_tools == {"retrieve_ordered_context"}
    assert "\n".join(str(x) for x in cli_agent.instructions) == "\n".join(
        str(x) for x in web_agent.instructions
    )


def test_get_agent_can_disable_ephemeral_metadata(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_ephemeral_metadata=False,
        session_sqlite_path=tmp_path / "s.db",
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        skill_id="default_agent",
        entrypoint="cli",
    )
    inst = ag.instructions
    if isinstance(inst, list):
        flat = "\n".join(str(x) for x in inst)
    else:
        flat = str(inst)
    assert "【运行时临时上下文】" not in flat


def test_get_agent_context_builder_keeps_task_summary_out_of_static_instructions(
    tmp_path: Path,
) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(session_sqlite_path=tmp_path / "s.db")
    summary = TaskSummary(
        session_id="s1",
        task_id="task_20260425T000000Z_abcdef12",
        summary_text="- 当前任务目标：整理当前交付物",
        summary_version=1,
        covered_message_count=4,
        updated_at="2026-04-25T00:00:00+00:00",
    )
    task = TaskSegment(
        task_id=summary.task_id,
        session_id="s1",
        client_id="c1",
        user_id=None,
        primary_skill_id="default_agent",
        invoked_skills=["default_agent"],
        task_title="交付物整理",
        status="active",
        created_at="2026-04-25T00:00:00+00:00",
        updated_at="2026-04-25T00:00:00+00:00",
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        current_task_summary=summary,
        session_task_index=[task],
    )
    inst = ag.instructions
    flat = "\n".join(str(x) for x in inst) if isinstance(inst, list) else str(inst)
    assert "【当前任务前情提要】" not in flat
    assert "整理当前交付物" not in flat
    assert "【本 session 任务目录（短索引）】" not in flat


def test_get_agent_legacy_mode_injects_task_summary_and_index(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(session_sqlite_path=tmp_path / "s.db", enable_context_builder=False)
    summary = TaskSummary(
        session_id="s1",
        task_id="task_20260425T000000Z_abcdef12",
        summary_text="- 当前任务目标：整理当前交付物",
        summary_version=1,
        covered_message_count=4,
        updated_at="2026-04-25T00:00:00+00:00",
    )
    task = TaskSegment(
        task_id=summary.task_id,
        session_id="s1",
        client_id="c1",
        user_id=None,
        primary_skill_id="default_agent",
        invoked_skills=["default_agent"],
        task_title="交付物整理",
        status="active",
        created_at="2026-04-25T00:00:00+00:00",
        updated_at="2026-04-25T00:00:00+00:00",
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
        current_task_summary=summary,
        session_task_index=[task],
    )
    inst = ag.instructions
    flat = "\n".join(str(x) for x in inst) if isinstance(inst, list) else str(inst)
    assert "【当前任务前情提要】" in flat
    assert "整理当前交付物" in flat
    assert "【本 session 任务目录（短索引）】" in flat
