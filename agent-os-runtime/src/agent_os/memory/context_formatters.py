from __future__ import annotations

from typing import Any


def format_memory_hit_for_context(hit: Any, *, temporal_grounding: bool) -> str:
    text = str(getattr(hit, "text", hit))
    if not temporal_grounding:
        return text
    meta = getattr(hit, "metadata", {}) or {}
    recorded = meta.get("recorded_at") or meta.get("created_at") or "记录时间未知"
    source = meta.get("memory_source") or meta.get("source") or "unknown"
    return f"[记录于 {recorded} | 来源 {source}] {text}"


def format_hindsight_lines_for_context(
    query: str,
    lines: list[str],
    *,
    enable_synthesis: bool,
    synthesis_model: str | None,
    max_candidates: int,
) -> str:
    if not lines:
        return ""
    if not enable_synthesis:
        return "\n---\n".join(lines)
    from agent_os.memory.hindsight_synthesizer import synthesize_hindsight_context

    return synthesize_hindsight_context(
        query=query,
        candidates=lines,
        model=synthesis_model,
        max_candidates=max_candidates,
    )


def format_asset_hits_for_context(
    query: str,
    hits: list[Any],
    *,
    include_raw: bool,
    asset_type: str | None,
    temporal_grounding: bool,
    enable_synthesis: bool,
    synthesis_model: str | None,
    max_candidates: int,
) -> str:
    from agent_os.knowledge.asset_store import format_hits_for_agent

    if not hits:
        return "（无）"
    if not enable_synthesis:
        return format_hits_for_agent(
            hits,
            include_raw=include_raw,
            temporal_grounding=temporal_grounding,
        )
    from agent_os.knowledge.asset_synthesizer import synthesize_asset_context

    return synthesize_asset_context(
        query=query,
        hits=hits,
        include_raw=include_raw,
        asset_type=asset_type,
        model=synthesis_model,
        max_candidates=max_candidates,
    )
