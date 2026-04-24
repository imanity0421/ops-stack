from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from agno.tools import tool

from ops_agent.evaluator.golden import check_violations
from ops_agent.memory.classify import suggest_memory_lane
from ops_agent.mcp.fixture_probe import format_probe_for_agent, load_probe_data
from ops_agent.memory.controller import MemoryController
from ops_agent.memory.models import MemoryLane, UserFact

if TYPE_CHECKING:
    from ops_agent.knowledge.graphiti_reader import GraphitiReadService
    from ops_agent.knowledge.asset_store import AssetStore

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
    skill_id: str = "default_ops",
    incremental_tools: Optional[list[Callable[..., object]]] = None,
    enable_mem0_learning: bool = True,
    enable_hindsight: bool = True,
    enable_asset_store: bool = False,
    skill_compliance_dir: Path | None = None,
) -> list[Callable]:
    """绑定租户上下文后的记忆工具，供 Agno Agent 使用。

    exclude_tool_names：在 Manifest 白名单之后再剔除的工具 id（例如 Web 演示仅允许手动写入记忆）。
    """

    @tool(
        name="record_client_fact",
        description="记录客户长期有效的事实（如公司名、品类、价格带、渠道）。写入 Mem0。",
    )
    def record_client_fact(fact_text: str) -> str:
        fact = UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id=client_id,
            user_id=user_id,
            text=fact_text,
            fact_type="attribute",
        )
        r = controller.ingest_user_fact(fact)
        if r.dedup_skipped:
            return "duplicate_skip"
        return f"ok: {r.written_to}"

    @tool(
        name="record_client_preference",
        description="记录客户稳定偏好（如语气风格、禁忌、审美）。写入 Mem0。",
    )
    def record_client_preference(preference_text: str) -> str:
        fact = UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id=client_id,
            user_id=user_id,
            text=preference_text,
            fact_type="preference",
        )
        r = controller.ingest_user_fact(fact)
        if r.dedup_skipped:
            return "duplicate_skip"
        return f"ok: {r.written_to}"

    @tool(
        name="record_task_feedback",
        description="记录对本次交付物/方案的具体反馈（任务级）。写入 Hindsight。",
    )
    def record_task_feedback(
        feedback_text: str,
        task_id: str | None = None,
        deliverable_type: str | None = None,
        impact_on_preference: bool = False,
    ) -> str:
        fact = UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id=client_id,
            user_id=user_id,
            task_id=task_id,
            deliverable_type=deliverable_type,
            text=feedback_text,
            fact_type="feedback",
            impact_on_preference=impact_on_preference,
        )
        r = controller.ingest_user_fact(fact)
        if r.dedup_skipped:
            return "duplicate_skip"
        return f"ok: {r.written_to}"

    @tool(
        name="search_client_memory",
        description="仅检索 Mem0 中的客户画像与事实（第一层）。完整检索请优先用 retrieve_ordered_context。",
    )
    def search_client_memory(query: str) -> str:
        hits = controller.search_profile(query, client_id=client_id, user_id=user_id, limit=8)
        if not hits:
            return "无匹配记忆。"
        lines = [h.text for h in hits]
        return "\n---\n".join(lines)

    @tool(
        name="search_past_lessons",
        description="仅检索 Hindsight 中的历史反馈与复盘教训（第二层）。完整检索请优先用 retrieve_ordered_context。",
    )
    def search_past_lessons(query: str) -> str:
        lines = controller.search_hindsight(query, client_id=client_id, limit=8)
        if not lines:
            return "无匹配历史教训或反馈。"
        return "\n---\n".join(lines)

    @tool(
        name="search_reference_cases",
        description="检索参考案例库（Asset Store，整案 few-shot 语感参考）。运行时仅检索，不做清洗与入库治理。",
    )
    def search_reference_cases(query: str, limit: int = 3, include_raw: bool = False) -> str:
        if not enable_asset_store or asset_store is None:
            return "（当前未启用案例库 Asset Store）"
        from ops_agent.knowledge.asset_store import format_hits_for_agent

        hits = asset_store.search(
            query,
            client_id=client_id,
            user_id=user_id,
            skill_id=skill_id,
            limit=max(1, min(int(limit), 6)),
            include_raw=bool(include_raw),
        )
        return format_hits_for_agent(hits, include_raw=bool(include_raw))

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
        name="fetch_ops_probe_context",
        description="读取运营探针（市场/合规 fixture 或 OPS_MCP_PROBE_FIXTURE_PATH），作策略回答的旁路参考。",
    )
    def fetch_ops_probe_context() -> str:
        data = load_probe_data(mcp_probe_fixture_path)
        return format_probe_for_agent(data)

    @tool(
        name="retrieve_ordered_context",
        description="按固定顺序**检索**上下文：① Mem0 客户画像 ② Hindsight 历史教训 ③ Graphiti 领域知识（若已配置）④ Asset Store 参考案例（若已配置）。多源冲突时如何整合到最终回复，须遵守系统指令中的「宪法·冲突解决序」（与检索顺序不同）。回答策略/方案类问题前应优先调用本工具。",
    )
    def retrieve_ordered_context(query: str) -> str:
        blocks: list[str] = []
        mem = controller.search_profile(query, client_id=client_id, user_id=user_id, limit=8)
        blocks.append(
            "## ① 客户画像 (Mem0)\n" + ("\n---\n".join(h.text for h in mem) if mem else "（无）")
        )
        if enable_hindsight:
            hs = controller.search_hindsight(query, client_id=client_id, limit=8)
            blocks.append(
                "## ② 历史教训与反馈 (Hindsight)\n" + ("\n---\n".join(hs) if hs else "（无）")
            )
        else:
            blocks.append("## ② 历史教训与反馈 (Hindsight)\n（当前未启用）")
        if knowledge is not None:
            dom = knowledge.search_domain_knowledge(query, client_id=client_id, skill_id=skill_id)
            blocks.append("## ③ 领域知识 (Graphiti / 降级)\n" + dom)
        else:
            blocks.append("## ③ 领域知识 (Graphiti)\n（当前未挂载 Graphiti，依赖模型常识）")

        if enable_asset_store and asset_store is not None:
            from ops_agent.knowledge.asset_store import format_hits_for_agent

            hits = asset_store.search(
                query,
                client_id=client_id,
                user_id=user_id,
                skill_id=skill_id,
                limit=3,
                include_raw=False,
            )
            blocks.append(
                "## ④ 参考案例 (Asset Store)\n" + format_hits_for_agent(hits, include_raw=False)
            )
        else:
            blocks.append("## ④ 参考案例 (Asset Store)\n（当前未启用）")
        return "\n\n".join(blocks)

    tools: list[Callable] = [
        suggest_memory_lane_tool,
        fetch_ops_probe_context,
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
            description="按 OPS_GOLDEN_RULES_PATH 加载的正则规则检查交付文案是否命中禁忌表述。",
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
            description="按 OPS_SKILL_COMPLIANCE_DIR/<skill_id>.json 校验文案是否违反该 skill 硬规则（与 asset-ingest 入库合规同源）。",
        )
        def check_skill_compliance_text(text: str) -> str:
            from ops_agent.knowledge.skill_compliance import check_skill_compliance

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
