from __future__ import annotations

from typing import Any

from agent_os.memory.hindsight_retrieval import query_features
from agent_os.memory.models import MemorySearchHit


def token_overlap_count(query: str, text: str) -> int:
    q = query_features("" if query is None else str(query))
    t = query_features("" if text is None else str(text))
    if not q:
        return 0
    return len(q & t)


def abstain_mem0_hit(query: str, hit: MemorySearchHit, *, min_overlap: int) -> bool:
    """True = do not inject this hit (low relevance)."""
    if min_overlap <= 0:
        return False
    if not (query or "").strip():
        return False
    return token_overlap_count(query, hit.text) < min_overlap


def abstain_graphiti_text(
    query: str,
    text: str,
    *,
    min_overlap: int,
    strict_min_overlap: int,
    is_legacy_or_fallback: bool,
) -> bool:
    """True = abstain from injecting Graphiti / fallback blob."""
    t = (text or "").strip()
    if not t:
        return True
    # Legacy compat 哨兵块极短且承载权威元数据，不做 overlap 拒绝
    if "legacy client-skill group" in t:
        return False
    if "无权访问" in t or "未配置" in t or "无可用结果" in t:
        return False
    if not (query or "").strip():
        return False
    need = strict_min_overlap if is_legacy_or_fallback else min_overlap
    return token_overlap_count(query, t) < need


def abstain_asset_hit(
    hit: Any,
    query: str,
    *,
    min_overlap: int,
    max_l2_distance: float | None,
) -> bool:
    """True = drop this asset hit."""
    if min_overlap > 0 and (query or "").strip():
        blob = " ".join(
            [
                str(getattr(hit, "summary", "") or ""),
                str(getattr(hit, "feature_summary", "") or ""),
                str(getattr(hit, "style_fingerprint", "") or ""),
                " ".join(getattr(hit, "tags", []) or []),
            ]
        )
        if token_overlap_count(query, blob) < min_overlap:
            return True
    dist = getattr(hit, "score", None)
    if max_l2_distance is not None and max_l2_distance > 0 and dist is not None:
        try:
            if float(dist) > float(max_l2_distance):
                return True
        except (TypeError, ValueError):
            pass
    return False


def abstain_hindsight_line(query: str, line: str, *, min_overlap: int) -> bool:
    """True = drop this rendered hindsight line."""
    if min_overlap <= 0:
        return False
    if not (query or "").strip():
        return False
    base = line
    if " [score=" in base:
        base = base.split(" [score=", 1)[0]
    return token_overlap_count(query, base) < min_overlap
