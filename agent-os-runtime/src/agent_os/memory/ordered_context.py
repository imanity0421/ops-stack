from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Protocol

from agent_os.memory.context_formatters import (
    format_asset_hits_for_context,
    format_hindsight_lines_for_context,
)
from agent_os.memory.controller import MemoryController
from agent_os.memory.query_plan import RetrievalSubqueries, plan_retrieval_subqueries
from agent_os.memory.relevance_gate import (
    abstain_asset_hit,
    abstain_graphiti_text,
    abstain_hindsight_line,
    abstain_mem0_hit,
)


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
    hindsight_include_superseded: bool = False
    mem_limit: int = 8
    hindsight_limit: int = 8
    asset_limit: int = 3
    #: P2-6 低相关门控；关闭后保留旧行为（全量注入排序结果）
    enable_abstain_gate: bool = True
    abstain_min_query_overlap: int = 1
    #: Hindsight 行级 overlap；默认 0（检索已按同 query 排序，避免 query 词面窄时误杀相关教训）
    abstain_hindsight_min_query_overlap: int = 0
    abstain_graphiti_fallback_min_overlap: int = 2
    #: Lance 距离上界；None 或 <=0 表示不按距离剔除
    abstain_asset_max_l2: float | None = None
    #: Asset 层 token overlap 门槛；默认 0（仅靠向量序 + 可选 L2），避免中英特征集不交时误杀
    abstain_asset_min_query_overlap: int = 0
    #: P2-11 是否在包内输出 query_plan（轻量属性块）
    show_query_plan: bool = True
    #: P2-13 是否在包末追加 JIT 提示
    show_jit_hints: bool = True


def _xml_text(value: object) -> str:
    return escape("" if value is None else str(value), quote=False)


def _xml_attr(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _text_or_empty(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _safe_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return [value]


def _safe_call_list(fn: Any, *args: Any, **kwargs: Any) -> tuple[list[Any], str | None]:
    try:
        return _safe_list(fn(*args, **kwargs)), None
    except Exception as exc:
        return [], exc.__class__.__name__


def _safe_call_text(fn: Any, *args: Any, **kwargs: Any) -> tuple[str, str | None]:
    try:
        return _text_or_empty(fn(*args, **kwargs)), None
    except Exception as exc:
        return "", exc.__class__.__name__


def _attrs(**values: object) -> str:
    pairs = []
    for key, value in values.items():
        if value is None:
            continue
        pairs.append(f'{key}="{_xml_attr(value)}"')
    return (" " + " ".join(pairs)) if pairs else ""


def _item(tag: str, content: object, **attrs: object) -> str:
    return f"<{tag}{_attrs(**attrs)}>{_xml_text(content)}</{tag}>"


def _memory_item(hit: Any, idx: int) -> str:
    meta = getattr(hit, "metadata", {}) or {}
    recorded = meta.get("recorded_at") or meta.get("created_at") or "unknown"
    source = meta.get("memory_source") or meta.get("source") or "mem0"
    scope = meta.get("scope") or "profile"
    score = getattr(hit, "score", None)
    text = getattr(hit, "text", hit)
    return _item(
        "memory_item",
        text,
        index=idx,
        source=source,
        scope=scope,
        timestamp=recorded,
        authority="profile_fact",
        usage_rule="background_only",
        relevance=score if score is not None else "ranked",
    )


def _asset_item(hit: Any, idx: int) -> str:
    asset_type = getattr(hit, "asset_type", None) or "style_reference"
    usage_rule = "style_only" if asset_type == "style_reference" else "source_material_only"
    parts = [
        f"<summary>{_xml_text(getattr(hit, 'summary', ''))}</summary>",
        f"<style_fingerprint>{_xml_text(getattr(hit, 'style_fingerprint', ''))}</style_fingerprint>",
    ]
    feature = getattr(hit, "feature_summary", None)
    if feature:
        parts.append(f"<feature_summary>{_xml_text(feature)}</feature_summary>")
    excerpts = getattr(hit, "key_excerpts", []) or []
    if excerpts:
        body = "\n".join(_item("excerpt", x, index=i + 1) for i, x in enumerate(excerpts))
        parts.append(f"<key_excerpts>\n{body}\n</key_excerpts>")
    attrs = _attrs(
        index=idx,
        source="asset_store",
        case_id=getattr(hit, "case_id", None),
        scope=getattr(hit, "scope", None),
        timestamp=getattr(hit, "created_at", None),
        authority=asset_type,
        usage_rule=usage_rule,
        relevance=getattr(hit, "score", None),
    )
    return f"<asset_item{attrs}>\n" + "\n".join(parts) + "\n</asset_item>"


def _section(tag: str, content: str, **attrs: object) -> str:
    return f"<{tag}{_attrs(**attrs)}>\n{content}\n</{tag}>"


def _error_marker(layer: str, error: str) -> str:
    return f'<error layer="{_xml_attr(layer)}" type="{_xml_attr(error)}" />'


def _graphiti_attrs(text: str) -> dict[str, str]:
    t = _text_or_empty(text)
    if "legacy client-skill group" in t:
        return {
            "source": "graphiti",
            "scope": "legacy_client_skill",
            "authority": "legacy_compat",
            "usage_rule": "background_only",
            "relevance": "legacy_compat",
        }
    if t.startswith("[降级]"):
        return {
            "source": "graphiti_fallback",
            "scope": "system",
            "authority": "fallback_knowledge",
            "usage_rule": "background_only",
            "relevance": "fallback",
        }
    return {
        "source": "graphiti",
        "scope": "system",
        "authority": "domain_knowledge",
        "usage_rule": "background_only",
        "relevance": "ranked",
    }


def _graphiti_legacy_or_fallback(g_attrs: dict[str, str]) -> bool:
    return g_attrs.get("authority") in ("legacy_compat", "fallback_knowledge")


def _query_plan_xml(sq: RetrievalSubqueries) -> str:
    return (
        "<query_plan"
        + _attrs(
            raw=sq.raw,
            profile_query=sq.profile,
            lesson_query=sq.lesson,
            hindsight_used_query="raw",
            knowledge_query=sq.knowledge,
            style_query=sq.style,
            material_query=sq.material,
        )
        + " />"
    )


def _jit_xml(*, graphiti_injected: bool, asset_injected: bool) -> str:
    parts: list[str] = []
    if graphiti_injected:
        parts.append(
            '<jit_hint layer="graphiti">更长或更细领域知识可后续调用 search_domain_knowledge 按需加载。</jit_hint>'
        )
    if asset_injected:
        parts.append(
            '<jit_hint layer="asset_store">完整案例正文可按需通过 search_reference_cases(include_raw=true) 或结合 case_id 精确定位加载。</jit_hint>'
        )
    return "\n".join(parts)


def _asset_section(
    *,
    tag: str,
    query: str,
    hits: list[Any],
    asset_type: str,
    temporal_grounding: bool,
    enable_synthesis: bool,
    synthesis_model: str | None,
    max_candidates: int,
    abstained_count: int = 0,
) -> str:
    usage_rule = "style_only" if asset_type == "style_reference" else "source_material_only"
    if hits and enable_synthesis:
        body = _item(
            "asset_summary",
            format_asset_hits_for_context(
                query,
                hits,
                include_raw=False,
                asset_type=asset_type,
                temporal_grounding=temporal_grounding,
                enable_synthesis=True,
                synthesis_model=synthesis_model,
                max_candidates=max_candidates,
            ),
            source="asset_store",
            authority=asset_type,
            usage_rule=usage_rule,
            relevance="synthesized",
        )
    else:
        body = "\n".join(_asset_item(h, i + 1) for i, h in enumerate(hits)) or "<empty />"
    return _section(
        tag,
        body,
        source="asset_store",
        authority=asset_type,
        usage_rule=usage_rule,
        relevance="ranked" if hits else "empty",
        abstained_count=abstained_count or None,
    )


def render_retrieve_ordered_context_markdown(
    controller: MemoryController,
    query: str,
    options: RetrieveOrderedContextOptions,
) -> str:
    """
    按 Mem0 → Hindsight → Graphiti → Asset 固定顺序组装 XML-like 证据包。

    供 ``MemoryController.retrieve_ordered_context`` 与集成测试调用；保留旧函数名避免
    破坏调用方，输出格式已在 Context V2 P2 升级为结构化 evidence bundle。
    """
    o = options
    sq = plan_retrieval_subqueries(query)
    profile_q = sq.profile
    knowledge_q = sq.knowledge
    style_q = sq.style
    material_q = sq.material

    blocks: list[str] = []
    if o.show_query_plan:
        blocks.append(_query_plan_xml(sq))

    mem_raw, mem_error = _safe_call_list(
        controller.search_profile,
        profile_q,
        client_id=o.client_id,
        user_id=o.user_id,
        limit=o.mem_limit,
    )
    mem = mem_raw
    if o.enable_abstain_gate:
        mem = [
            h
            for h in mem
            if not abstain_mem0_hit(sq.raw, h, min_overlap=o.abstain_min_query_overlap)
        ]
    mem_abstained = len(mem_raw) - len(mem)
    mem_relevance = "error" if mem_error else ("empty" if not mem else "ranked")
    blocks.append(
        _section(
            "mem0_profile",
            (
                _error_marker("mem0", mem_error)
                if mem_error
                else "\n".join(_memory_item(h, i + 1) for i, h in enumerate(mem)) or "<empty />"
            ),
            source="mem0",
            authority="profile_fact",
            usage_rule="background_only",
            relevance=mem_relevance,
            abstained_count=mem_abstained or None,
            error=mem_error,
        )
    )

    if o.enable_hindsight:
        # 检索仍用 raw：扩写 lesson_q 易把英文教训挤出 Top-K；扩写仅体现在 query_plan.lesson_query
        hs_raw, hs_error = _safe_call_list(
            controller.search_hindsight,
            sq.raw or (query or "").strip(),
            client_id=o.client_id,
            limit=o.hindsight_limit,
            user_id=o.user_id,
            skill_id=o.skill_id,
            temporal_grounding=o.enable_temporal_grounding,
            debug_scores=o.hindsight_debug_scores,
            include_superseded=o.hindsight_include_superseded or o.hindsight_debug_scores,
        )
        hs = hs_raw
        if o.enable_abstain_gate and not o.hindsight_debug_scores:
            hs = [
                ln
                for ln in hs
                if not abstain_hindsight_line(
                    sq.raw, ln, min_overlap=o.abstain_hindsight_min_query_overlap
                )
            ]
        hs_abstained = len(hs_raw) - len(hs)
        if hs_error:
            hindsight_body = _error_marker("hindsight", hs_error)
        elif hs and o.enable_hindsight_synthesis and not o.hindsight_debug_scores:
            hindsight_body = _item(
                "lesson_summary",
                format_hindsight_lines_for_context(
                    sq.raw or (query or "").strip(),
                    hs,
                    enable_synthesis=True,
                    synthesis_model=o.hindsight_synthesis_model,
                    max_candidates=o.hindsight_synthesis_max_candidates,
                ),
                source="hindsight",
                scope="task_scoped",
                authority="lesson",
                usage_rule="lesson_only",
                relevance="synthesized",
            )
        else:
            hindsight_body = (
                "\n".join(
                    _item(
                        "lesson_item",
                        line,
                        index=i + 1,
                        source="hindsight",
                        scope="task_scoped",
                        authority="lesson",
                        usage_rule="lesson_only",
                        relevance="ranked",
                    )
                    for i, line in enumerate(hs)
                )
                if hs
                else "<empty />"
            )
        blocks.append(
            _section(
                "hindsight_lessons",
                hindsight_body,
                source="hindsight",
                authority="lesson",
                usage_rule="lesson_only",
                relevance="error" if hs_error else ("ranked" if hs else "empty"),
                abstained_count=hs_abstained or None,
                error=hs_error,
            )
        )
    else:
        blocks.append(
            _section(
                "hindsight_lessons",
                "<disabled />",
                source="hindsight",
                authority="lesson",
                usage_rule="lesson_only",
                relevance="disabled",
            )
        )

    graphiti_jit = False
    if o.knowledge is not None:
        dom, dom_error = _safe_call_text(
            o.knowledge.search_domain_knowledge,
            knowledge_q,
            client_id=o.client_id,
            skill_id=o.skill_id,
        )
        g_attrs = _graphiti_attrs(dom)
        graphiti_empty = not dom.strip()
        graphiti_abstain = (
            (not dom_error)
            and (not graphiti_empty)
            and o.enable_abstain_gate
            and abstain_graphiti_text(
            knowledge_q,
            dom,
            min_overlap=o.abstain_min_query_overlap,
            strict_min_overlap=o.abstain_graphiti_fallback_min_overlap,
            is_legacy_or_fallback=_graphiti_legacy_or_fallback(g_attrs),
        )
        )
        if dom_error:
            blocks.append(
                _section(
                    "graphiti_knowledge",
                    _error_marker("graphiti", dom_error),
                    source="graphiti",
                    scope="system",
                    authority="domain_knowledge",
                    usage_rule="background_only",
                    relevance="error",
                    error=dom_error,
                )
            )
        elif graphiti_empty:
            blocks.append(
                _section(
                    "graphiti_knowledge",
                    "<empty />",
                    source="graphiti",
                    scope="system",
                    authority="domain_knowledge",
                    usage_rule="background_only",
                    relevance="empty",
                )
            )
        elif graphiti_abstain:
            blocks.append(
                _section(
                    "graphiti_knowledge",
                    "<abstained />",
                    source=g_attrs["source"],
                    scope=g_attrs.get("scope"),
                    authority=g_attrs["authority"],
                    usage_rule=g_attrs["usage_rule"],
                    relevance="abstained",
                    abstained_count=1,
                )
            )
        else:
            graphiti_jit = True
            blocks.append(
                _section(
                    "graphiti_knowledge",
                    _item(
                        "knowledge_block",
                        dom,
                        **g_attrs,
                    ),
                    source=g_attrs["source"],
                    authority=g_attrs["authority"],
                    usage_rule=g_attrs["usage_rule"],
                    relevance=g_attrs["relevance"],
                )
            )
    else:
        blocks.append(
            _section(
                "graphiti_knowledge",
                "<unmounted />",
                source="graphiti",
                authority="domain_knowledge",
                usage_rule="background_only",
                relevance="unmounted",
            )
        )

    asset_jit = False
    if o.enable_asset_store and o.asset_store is not None:
        style_raw, style_error = _safe_call_list(
            o.asset_store.search,
            style_q,
            client_id=o.client_id,
            user_id=o.user_id,
            skill_id=o.skill_id,
            limit=o.asset_limit,
            include_raw=False,
            asset_type="style_reference",
        )
        source_raw, source_error = _safe_call_list(
            o.asset_store.search,
            material_q,
            client_id=o.client_id,
            user_id=o.user_id,
            skill_id=o.skill_id,
            limit=o.asset_limit,
            include_raw=False,
            asset_type="source_material",
        )
        style_hits = style_raw
        source_hits = source_raw
        if o.enable_abstain_gate:
            style_gate_q = f"{sq.raw} {style_q}".strip()
            material_gate_q = f"{sq.raw} {material_q}".strip()
            style_hits = [
                h
                for h in style_hits
                if not abstain_asset_hit(
                    h,
                    style_gate_q,
                    min_overlap=o.abstain_asset_min_query_overlap,
                    max_l2_distance=o.abstain_asset_max_l2,
                )
            ]
            source_hits = [
                h
                for h in source_hits
                if not abstain_asset_hit(
                    h,
                    material_gate_q,
                    min_overlap=o.abstain_asset_min_query_overlap,
                    max_l2_distance=o.abstain_asset_max_l2,
                )
            ]
        style_abstained = len(style_raw) - len(style_hits)
        source_abstained = len(source_raw) - len(source_hits)
        if style_hits or source_hits:
            asset_jit = True
        blocks.append(
            _section(
                "asset_references",
                "\n\n".join(
                    [
                        _section(
                            "style_references",
                            _error_marker("asset_style_reference", style_error),
                            source="asset_store",
                            authority="style_reference",
                            usage_rule="style_only",
                            relevance="error",
                            error=style_error,
                        )
                        if style_error
                        else _asset_section(
                            tag="style_references",
                            query=style_q,
                            hits=style_hits,
                            asset_type="style_reference",
                            temporal_grounding=o.enable_temporal_grounding,
                            enable_synthesis=o.enable_asset_synthesis,
                            synthesis_model=o.asset_synthesis_model,
                            max_candidates=o.asset_synthesis_max_candidates,
                            abstained_count=style_abstained,
                        ),
                        _section(
                            "source_materials",
                            _error_marker("asset_source_material", source_error),
                            source="asset_store",
                            authority="source_material",
                            usage_rule="source_material_only",
                            relevance="error",
                            error=source_error,
                        )
                        if source_error
                        else _asset_section(
                            tag="source_materials",
                            query=material_q,
                            hits=source_hits,
                            asset_type="source_material",
                            temporal_grounding=o.enable_temporal_grounding,
                            enable_synthesis=o.enable_asset_synthesis,
                            synthesis_model=o.asset_synthesis_model,
                            max_candidates=o.asset_synthesis_max_candidates,
                            abstained_count=source_abstained,
                        ),
                    ]
                ),
                source="asset_store",
                authority="asset_reference",
                usage_rule="reference_only",
                relevance=(
                    "error"
                    if style_error or source_error
                    else ("ranked" if style_hits or source_hits else "empty")
                ),
                abstained_count=(style_abstained + source_abstained) or None,
                error=";".join(e for e in (style_error, source_error) if e) or None,
            )
        )
    else:
        blocks.append(
            _section(
                "asset_references",
                "<disabled />",
                source="asset_store",
                authority="asset_reference",
                usage_rule="reference_only",
                relevance="disabled",
            )
        )

    has_injected_evidence = bool(mem or (o.enable_hindsight and hs) or graphiti_jit or asset_jit)
    if o.show_jit_hints:
        jit = _jit_xml(graphiti_injected=graphiti_jit, asset_injected=asset_jit)
        if jit:
            blocks.append(jit)

    return (
        '<ordered_context format="xml_like" version="2.2" '
        f'query="{_xml_attr(query)}" usage_rule="evidence_only" '
        f'injected_evidence="{str(has_injected_evidence).lower()}">\n'
        + "\n\n".join(blocks)
        + "\n</ordered_context>"
    )
