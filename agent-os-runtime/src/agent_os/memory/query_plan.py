from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalSubqueries:
    """Deterministic per-layer query strings derived from the user/tool query."""

    raw: str
    profile: str
    lesson: str
    knowledge: str
    style: str
    material: str


def plan_retrieval_subqueries(user_query: str) -> RetrievalSubqueries:
    """Lightweight query planning (P2-11): template expansion per memory layer."""
    q = " ".join(("" if user_query is None else str(user_query)).strip().split())
    if not q:
        empty = ""
        return RetrievalSubqueries(
            raw=empty,
            profile=empty,
            lesson=empty,
            knowledge=empty,
            style=empty,
            material=empty,
        )
    return RetrievalSubqueries(
        raw=q,
        profile=q,
        lesson=f"{q} 复盘 教训 风险 约束",
        knowledge=f"{q} SOP 流程 规范 领域知识",
        style=f"{q} 写作风格 语气 结构 节奏",
        material=f"{q} 事实 细节 背景 素材 数据",
    )
