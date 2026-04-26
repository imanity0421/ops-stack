from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from agno.tools import tool

from agent_os.evaluator.golden import check_violations
from agent_os.memory.classify import suggest_memory_lane
from agent_os.memory.context_formatters import (
    format_asset_hits_for_context,
    format_hindsight_lines_for_context,
    format_memory_hit_for_context,
)
from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemoryLane, UserFact
from agent_os.memory.ordered_context import RetrieveOrderedContextOptions
from agent_os.mcp.fixture_probe import format_probe_for_agent, load_probe_data

if TYPE_CHECKING:
    from agent_os.knowledge.graphiti_reader import GraphitiReadService
    from agent_os.knowledge.asset_store import AssetStore

logger = logging.getLogger(__name__)


def _tool_name(fn: Callable) -> str:
    return str(getattr(fn, "name", None) or getattr(fn, "__name__", ""))


def filter_tools_by_manifest(tools: list[Callable], enabled: set[str] | None) -> list[Callable]:
    """按 Manifest enabled_tools 子集筛选；为空集或 None 则不过滤。"""
    if not enabled:
        return tools
    out = [t for t in tools if _tool_name(t) in enabled]
    if not out:
        logger.warning("enabled_tools 与当前工具无交集，回退为全部工具")
        return tools
    return out


def _bounded_int(value: object, *, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


def _clean_required_text(value: str) -> str | None:
    text = (value or "").strip()
    return text or None


def build_memory_tools(
    controller: MemoryController,
    client_id: str,
    user_id: str | None,
    knowledge: Optional["GraphitiReadService"] = None,
    asset_store: Optional["AssetStore"] = None,
    golden_rules: Optional[list[dict[str, Any]]] = None,
    mcp_probe_fixture_path: Path | None = None,
    enabled_tool_names: set[str] | None = None,
    exclude_tool_names: set[str] | None = None,
    *,
    skill_id: str = "default_agent",
    incremental_tools: Optional[list[Callable[..., object]]] = None,
    enable_mem0_learning: bool = True,
    enable_hindsight: bool = True,
    enable_asset_store: bool = False,
    enable_temporal_grounding: bool = True,
    enable_hindsight_synthesis: bool = False,
    hindsight_synthesis_model: str | None = None,
    hindsight_synthesis_max_candidates: int = 20,
    enable_asset_synthesis: bool = False,
    asset_synthesis_model: str | None = None,
    asset_synthesis_max_candidates: int = 12,
    skill_compliance_dir: Path | None = None,
    enable_hindsight_debug_tools: bool = False,
) -> list[Callable]:
    """绑定租户上下文后的记忆工具，供 Agno Agent 使用。

    exclude_tool_names：在 Manifest 白名单之后再剔除的工具 id（例如 Web 演示仅允许手动写入记忆）。
    """

    _retrieve_ordered_opts = RetrieveOrderedContextOptions(
        client_id=client_id,
        user_id=user_id,
        skill_id=skill_id,
        enable_hindsight=enable_hindsight,
        enable_temporal_grounding=enable_temporal_grounding,
        knowledge=knowledge,
        enable_asset_store=enable_asset_store,
        asset_store=asset_store,
        enable_hindsight_synthesis=enable_hindsight_synthesis,
        hindsight_synthesis_model=hindsight_synthesis_model,
        hindsight_synthesis_max_candidates=hindsight_synthesis_max_candidates,
        enable_asset_synthesis=enable_asset_synthesis,
        asset_synthesis_model=asset_synthesis_model,
        asset_synthesis_max_candidates=asset_synthesis_max_candidates,
    )

    @tool(
        name="record_client_fact",
        description=(
            "【特权写入】仅记录长期有效、未来多次任务都应复用的主体事实"
            "（组织名、稳定约束、默认流程、长期禁忌等）。禁止记录玩笑、临时任务、模糊推测、一次性素材；"
            "不确定时不要调用。写入 Mem0。"
        ),
    )
    def record_client_fact(fact_text: str) -> str:
        text = _clean_required_text(fact_text)
        if text is None:
            return "rejected: empty_text"
        fact = UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id=client_id,
            user_id=user_id,
            scope="client_shared",
            skill_id=skill_id,
            text=text,
            fact_type="attribute",
        )
        r = controller.ingest_user_fact(fact)
        if r.policy_rejected:
            return f"policy_rejected: {r.policy_reason or r.dedup_reason or 'unknown'}"
        if r.dedup_skipped:
            return "duplicate_skip"
        return f"ok: {r.written_to}"

    @tool(
        name="record_client_preference",
        description=(
            "【特权写入】仅记录用户或对话方明确表达的稳定偏好、禁忌或判断规则"
            "（默认语气、长期不要做的表达等）。禁止记录“这次先...”等一次性要求；不确定时不要调用。写入 Mem0。"
        ),
    )
    def record_client_preference(preference_text: str) -> str:
        text = _clean_required_text(preference_text)
        if text is None:
            return "rejected: empty_text"
        fact = UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id=client_id,
            user_id=user_id,
            scope="user_private" if user_id else "client_shared",
            skill_id=skill_id,
            text=text,
            fact_type="preference",
        )
        r = controller.ingest_user_fact(fact)
        if r.policy_rejected:
            return f"policy_rejected: {r.policy_reason or r.dedup_reason or 'unknown'}"
        if r.dedup_skipped:
            return "duplicate_skip"
        return f"ok: {r.written_to}"

    @tool(
        name="record_task_feedback",
        description=(
            "【特权写入】仅记录明确、可复盘、会影响后续方法的任务反馈或教训。"
            "禁止记录闲聊、情绪噪声、模糊夸奖或一次性改字需求。写入 Hindsight。"
            "可选 supersedes_event_id：填入既有 Hindsight 行的 event_id 表示本条取代该条（检索时隐藏被取代行）。"
            "可选 weight_count（1–10000，默认 1）：同类合并统计权重。"
        ),
    )
    def record_task_feedback(
        feedback_text: str,
        task_id: str | None = None,
        deliverable_type: str | None = None,
        impact_on_preference: bool = False,
        supersedes_event_id: str | None = None,
        weight_count: Any = 1,
    ) -> str:
        text = _clean_required_text(feedback_text)
        if text is None:
            return "rejected: empty_text"
        sid = (supersedes_event_id or "").strip() or None
        wc = _bounded_int(weight_count, default=1, lower=1, upper=10000)
        fact = UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id=client_id,
            user_id=user_id,
            scope="task_scoped",
            skill_id=skill_id,
            task_id=task_id,
            deliverable_type=deliverable_type,
            text=text,
            fact_type="feedback",
            impact_on_preference=impact_on_preference,
            supersedes_event_id=sid,
            weight_count=wc,
        )
        r = controller.ingest_user_fact(fact)
        if r.policy_rejected:
            return f"policy_rejected: {r.policy_reason or r.dedup_reason or 'unknown'}"
        if r.dedup_skipped:
            return "duplicate_skip"
        return f"ok: {r.written_to}"

    @tool(
        name="search_client_memory",
        description="仅检索 Mem0 中的主体画像与事实（第一层）。完整检索请优先用 retrieve_ordered_context。",
    )
    def search_client_memory(query: str) -> str:
        hits = controller.search_profile(query, client_id=client_id, user_id=user_id, limit=8)
        if not hits:
            return "无匹配记忆。"
        lines = [
            format_memory_hit_for_context(h, temporal_grounding=enable_temporal_grounding)
            for h in hits
        ]
        return "\n---\n".join(lines)

    @tool(
        name="search_past_lessons",
        description=(
            "仅检索 Hindsight 中的历史反馈与复盘教训（第二层）。完整检索请优先用 retrieve_ordered_context。"
            "debug_scores 默认 false；仅排查召回污染/排序问题时才设为 true。"
        ),
    )
    def search_past_lessons(query: str, debug_scores: bool = False) -> str:
        if debug_scores and not enable_hindsight_debug_tools:
            return "debug_scores_disabled: 需要显式启用 Hindsight 调试工具模式。"
        lines = controller.search_hindsight(
            query,
            client_id=client_id,
            limit=8,
            user_id=user_id,
            skill_id=skill_id,
            temporal_grounding=enable_temporal_grounding,
            debug_scores=bool(debug_scores),
        )
        if not lines:
            return "无匹配历史教训或反馈。"
        return format_hindsight_lines_for_context(
            query,
            lines,
            enable_synthesis=enable_hindsight_synthesis and not bool(debug_scores),
            synthesis_model=hindsight_synthesis_model,
            max_candidates=hindsight_synthesis_max_candidates,
        )

    @tool(
        name="search_reference_cases",
        description=(
            "检索资产库（Asset Store）。asset_type 可传 style_reference（风格/Few-shot）"
            "或 source_material（背景素材/故事资料）；运行时仅检索，不做清洗与入库治理。"
        ),
    )
    def search_reference_cases(
        query: str,
        limit: Any = 3,
        include_raw: bool = False,
        asset_type: str | None = None,
    ) -> str:
        if not enable_asset_store or asset_store is None:
            return "（当前未启用案例库 Asset Store）"

        at = asset_type if asset_type in ("style_reference", "source_material") else None
        hits = asset_store.search(
            query,
            client_id=client_id,
            user_id=user_id,
            skill_id=skill_id,
            limit=_bounded_int(limit, default=3, lower=1, upper=6),
            include_raw=bool(include_raw),
            asset_type=at,
        )
        return format_asset_hits_for_context(
            query,
            hits,
            include_raw=bool(include_raw),
            asset_type=at,
            temporal_grounding=enable_temporal_grounding,
            enable_synthesis=enable_asset_synthesis,
            synthesis_model=asset_synthesis_model,
            max_candidates=asset_synthesis_max_candidates,
        )

    @tool(
        name="suggest_memory_lane",
        description="对用户一句话做记忆槽启发式分类（任务反馈 vs 长期画像），不写入存储；不确定时请自行判断。",
    )
    def suggest_memory_lane_tool(utterance: str) -> str:
        lane, reason = suggest_memory_lane(utterance)
        if lane is None:
            return f"uncertain: {reason}"
        return f"lane={lane.value}: {reason}"

    @tool(
        name="fetch_probe_context",
        description="读取外部上下文探针（fixture 或 AGENT_OS_MCP_PROBE_FIXTURE_PATH），作为回答的旁路参考。",
    )
    def fetch_probe_context() -> str:
        data = load_probe_data(mcp_probe_fixture_path)
        return format_probe_for_agent(data)

    @tool(
        name="retrieve_ordered_context",
        description=(
            "按固定顺序**检索**上下文：① Mem0 主体画像 ② Hindsight 历史教训 ③ Graphiti 领域知识（若已配置）④ Asset Store 参考案例（若已配置）。"
            "多源冲突时如何整合到最终回复，须遵守系统指令中的「宪法·冲突解决序」（与检索顺序不同）。"
            "回答策略/方案类问题前应优先调用本工具。debug_scores 默认 false；仅排查 Hindsight 召回污染/排序问题时才设为 true。"
        ),
    )
    def retrieve_ordered_context(query: str, debug_scores: bool = False) -> str:
        if debug_scores and not enable_hindsight_debug_tools:
            return "debug_scores_disabled: 需要显式启用 Hindsight 调试工具模式。"
        opts = (
            replace(_retrieve_ordered_opts, hindsight_debug_scores=True)
            if bool(debug_scores)
            else _retrieve_ordered_opts
        )
        return controller.retrieve_ordered_context(query, opts)

    tools: list[Callable] = [
        suggest_memory_lane_tool,
        fetch_probe_context,
        retrieve_ordered_context,
        search_client_memory,
    ]

    if enable_mem0_learning:
        tools.extend([record_client_fact, record_client_preference])

    if enable_hindsight:
        tools.extend([record_task_feedback, search_past_lessons])

    if enable_asset_store:
        tools.append(search_reference_cases)

    rules = golden_rules or []
    if rules:

        @tool(
            name="check_delivery_text",
            description="按 AGENT_OS_GOLDEN_RULES_PATH 加载的正则规则检查交付文本是否命中禁忌表述。",
        )
        def check_delivery_text(text: str) -> str:
            v = check_violations(text, rules)
            if not v:
                return "ok: 未命中规则。"
            return "violations:\n" + "\n".join(v)

        tools.append(check_delivery_text)

    if knowledge is not None:

        @tool(
            name="search_domain_knowledge",
            description="仅检索 Graphiti 领域知识（第三层）。完整检索请优先用 retrieve_ordered_context。",
        )
        def search_domain_knowledge(query: str) -> str:
            return knowledge.search_domain_knowledge(query, client_id=client_id, skill_id=skill_id)

        tools.append(search_domain_knowledge)

    if skill_compliance_dir is not None:

        @tool(
            name="check_skill_compliance_text",
            description="按 AGENT_OS_SKILL_COMPLIANCE_DIR/<skill_id>.json 校验交付文本是否违反该 skill 硬规则（与 asset-ingest 入库合规同源）。",
        )
        def check_skill_compliance_text(text: str) -> str:
            from agent_os.knowledge.skill_compliance import check_skill_compliance

            v = check_skill_compliance(text, skill_id, skill_compliance_dir)
            if not v:
                return "ok: 未命中 skill 合规规则。"
            return "violations:\n" + "\n".join(v)

        tools.append(check_skill_compliance_text)

    if incremental_tools:
        tools.extend(incremental_tools)

    out = filter_tools_by_manifest(tools, enabled_tool_names)
    if exclude_tool_names:
        out = [t for t in out if _tool_name(t) not in exclude_tool_names]
    return out
