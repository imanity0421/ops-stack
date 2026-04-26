from __future__ import annotations

from pathlib import Path

from agent_os.knowledge.asset_store import AssetSearchHit
from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemoryLane, UserFact
from agent_os.memory.ordered_context import (
    RetrieveOrderedContextOptions,
    render_retrieve_ordered_context_markdown,
)


class _FakeKnowledge:
    def search_domain_knowledge(self, query: str, *, client_id: str, skill_id: str | None) -> str:
        return f"dom:{query}:{skill_id}"


class _FakeAssetStore:
    def search(self, *args, **kwargs):
        return [
            AssetSearchHit(
                case_id="asset-1",
                scope="system",
                asset_type="style_reference",
                summary="系统金牌案例：开头先抛冲突",
                feature_summary="痛点前置",
                style_fingerprint="短句、强钩子",
                primary_skill_hint="s1",
            )
        ]


def test_render_retrieve_ordered_context_markdown_layers(tmp_path: Path) -> None:
    local = tmp_path / "m.json"
    hind = tmp_path / "h.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
        enable_memory_policy=False,
    )
    opts = RetrieveOrderedContextOptions(
        client_id="c1",
        user_id="u1",
        skill_id="s1",
        enable_hindsight=True,
        enable_temporal_grounding=False,
        knowledge=_FakeKnowledge(),
        enable_asset_store=False,
        asset_store=None,
        enable_hindsight_synthesis=False,
        hindsight_synthesis_model=None,
        hindsight_synthesis_max_candidates=20,
        enable_asset_synthesis=False,
        asset_synthesis_model=None,
        asset_synthesis_max_candidates=12,
    )
    out = render_retrieve_ordered_context_markdown(ctrl, "q", opts)
    assert "## ① 主体画像 (Mem0)" in out
    assert "## ② 历史教训与反馈 (Hindsight)" in out
    assert "## ③ 领域知识 (Graphiti / 降级)" in out
    assert "dom:q:s1" in out
    assert "## ④ 参考案例 (Asset Store)" in out
    assert "（当前未启用）" in out


def test_memory_controller_retrieve_ordered_context_delegates(tmp_path: Path) -> None:
    local = tmp_path / "m.json"
    hind = tmp_path / "h.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
        enable_hindsight=False,
    )
    opts = RetrieveOrderedContextOptions(
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        enable_hindsight=False,
        enable_temporal_grounding=False,
        knowledge=None,
        enable_asset_store=False,
        asset_store=None,
        enable_hindsight_synthesis=False,
        hindsight_synthesis_model=None,
        hindsight_synthesis_max_candidates=20,
        enable_asset_synthesis=False,
        asset_synthesis_model=None,
        asset_synthesis_max_candidates=12,
    )
    out = ctrl.retrieve_ordered_context("x", opts)
    assert "（当前未启用）" in out
    assert "Graphiti" in out


def test_retrieve_ordered_context_smoke_includes_all_four_layers(tmp_path: Path) -> None:
    local = tmp_path / "m.json"
    hind = tmp_path / "h.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
        enable_memory_policy=False,
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="c1",
            user_id="u1",
            scope="client_shared",
            skill_id="s1",
            text="Stable delivery rule: list key constraints before drafting every deliverable",
            fact_type="attribute",
        )
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            user_id="u1",
            skill_id="s1",
            text="Historical feedback: open the plan with the conclusion first",
            fact_type="feedback",
        )
    )
    opts = RetrieveOrderedContextOptions(
        client_id="c1",
        user_id="u1",
        skill_id="s1",
        enable_hindsight=True,
        enable_temporal_grounding=False,
        knowledge=_FakeKnowledge(),
        enable_asset_store=True,
        asset_store=_FakeAssetStore(),
        enable_hindsight_synthesis=False,
        hindsight_synthesis_model=None,
        hindsight_synthesis_max_candidates=20,
        enable_asset_synthesis=False,
        asset_synthesis_model=None,
        asset_synthesis_max_candidates=12,
    )

    out = ctrl.retrieve_ordered_context("constraints", opts)

    assert "Stable delivery rule" in out
    assert "Historical feedback" in out
    assert "dom:constraints:s1" in out
    assert "系统金牌案例" in out
