from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent_os.memory.context_formatters import (
    format_asset_hits_for_context,
    format_hindsight_lines_for_context,
    format_memory_hit_for_context,
)
from agent_os.memory.controller import MemoryController


class DomainKnowledgePort(Protocol):
    def search_domain_knowledge(
        self, query: str, *, client_id: str, skill_id: str | None
    ) -> str: ...


class AssetStorePort(Protocol):
    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        limit: int,
        include_raw: bool,
        asset_type: str | None = None,
    ) -> list[Any]: ...


@dataclass(frozen=True)
class RetrieveOrderedContextOptions:
    """``retrieve_ordered_context`` 工具的运行时选项（与租户 / Graphiti / Asset 挂载一致）。"""

    client_id: str
    user_id: str | None
    skill_id: str
    enable_hindsight: bool
    enable_temporal_grounding: bool
    knowledge: DomainKnowledgePort | None
    enable_asset_store: bool
    asset_store: AssetStorePort | None
    enable_hindsight_synthesis: bool
    hindsight_synthesis_model: str | None
    hindsight_synthesis_max_candidates: int
    enable_asset_synthesis: bool
    asset_synthesis_model: str | None
    asset_synthesis_max_candidates: int
    hindsight_debug_scores: bool = False
    mem_limit: int = 8
    hindsight_limit: int = 8
    asset_limit: int = 3


def render_retrieve_ordered_context_markdown(
    controller: MemoryController,
    query: str,
    options: RetrieveOrderedContextOptions,
) -> str:
    """
    按 Mem0 → Hindsight → Graphiti → Asset 固定顺序组装 Markdown 块。

    供 ``MemoryController.retrieve_ordered_context`` 与集成测试调用。
    """
    o = options
    blocks: list[str] = []

    mem = controller.search_profile(
        query, client_id=o.client_id, user_id=o.user_id, limit=o.mem_limit
    )
    blocks.append(
        "## ① 主体画像 (Mem0)\n"
        + (
            "\n---\n".join(
                format_memory_hit_for_context(h, temporal_grounding=o.enable_temporal_grounding)
                for h in mem
            )
            if mem
            else "（无）"
        )
    )

    if o.enable_hindsight:
        hs = controller.search_hindsight(
            query,
            client_id=o.client_id,
            limit=o.hindsight_limit,
            user_id=o.user_id,
            skill_id=o.skill_id,
            temporal_grounding=o.enable_temporal_grounding,
            debug_scores=o.hindsight_debug_scores,
        )
        blocks.append(
            "## ② 历史教训与反馈 (Hindsight)\n"
            + (
                format_hindsight_lines_for_context(
                    query,
                    hs,
                    enable_synthesis=o.enable_hindsight_synthesis
                    and not o.hindsight_debug_scores,
                    synthesis_model=o.hindsight_synthesis_model,
                    max_candidates=o.hindsight_synthesis_max_candidates,
                )
                if hs
                else "（无）"
            )
        )
    else:
        blocks.append("## ② 历史教训与反馈 (Hindsight)\n（当前未启用）")

    if o.knowledge is not None:
        dom = o.knowledge.search_domain_knowledge(query, client_id=o.client_id, skill_id=o.skill_id)
        blocks.append("## ③ 领域知识 (Graphiti / 降级)\n" + dom)
    else:
        blocks.append("## ③ 领域知识 (Graphiti)\n（当前未挂载 Graphiti，依赖模型常识）")

    if o.enable_asset_store and o.asset_store is not None:
        hits = o.asset_store.search(
            query,
            client_id=o.client_id,
            user_id=o.user_id,
            skill_id=o.skill_id,
            limit=o.asset_limit,
            include_raw=False,
            asset_type=None,
        )
        blocks.append(
            "## ④ 参考案例 (Asset Store)\n"
            + format_asset_hits_for_context(
                query,
                hits,
                include_raw=False,
                asset_type=None,
                temporal_grounding=o.enable_temporal_grounding,
                enable_synthesis=o.enable_asset_synthesis,
                synthesis_model=o.asset_synthesis_model,
                max_candidates=o.asset_synthesis_max_candidates,
            )
        )
    else:
        blocks.append("## ④ 参考案例 (Asset Store)\n（当前未启用）")

    return "\n\n".join(blocks)
