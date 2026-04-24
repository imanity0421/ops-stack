from __future__ import annotations

import json
from pathlib import Path

from ops_agent.agent.constitutional import MARKER, build_constitutional_instruction_blocks
from ops_agent.agent.factory import get_agent
from ops_agent.config import Settings
from ops_agent.manifest_loader import AgentManifestV1, load_skill_manifest_registry
from ops_agent.manifest_output import OpsPlanStructuredV1, resolve_structured_output_model
from ops_agent.memory.controller import MemoryController


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


def test_ops_plan_structured_v1_roundtrip_five_times() -> None:
    for i in range(5):
        m = OpsPlanStructuredV1(
            title=f"标题{i}",
            outline=[f"点A{i}", f"点B{i}"],
            key_messages=[f"k{i}"],
            body_markdown=f"正文{i}" * 20,
        )
        raw = json.dumps(m.model_dump(), ensure_ascii=False)
        OpsPlanStructuredV1.model_validate_json(raw)


def test_resolve_structured_v1_from_manifest() -> None:
    m = AgentManifestV1(
        output_mode="structured_v1",
        output_schema_version="1.0",
    )
    assert resolve_structured_output_model(m) is OpsPlanStructuredV1
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


def test_packaged_planning_draft_manifest_loads() -> None:
    reg = load_skill_manifest_registry()
    m = reg.get("planning_draft")
    assert m is not None
    assert m.output_mode == "structured_v1"
    assert resolve_structured_output_model(m) is OpsPlanStructuredV1


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
        skill_id="default_ops",
    )
    inst = ag.instructions
    if isinstance(inst, list):
        flat = "\n".join(str(x) for x in inst)
    else:
        flat = str(inst)
    assert MARKER not in flat
