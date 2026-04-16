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
    golden_rules: Optional[list[dict[str, Any]]] = None,
    mcp_probe_fixture_path: Path | None = None,
    enabled_tool_names: set[str] | None = None,
) -> list[Callable]:
    """绑定租户上下文后的记忆工具，供 Agno Agent 使用。"""

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
        description="按固定顺序检索上下文：① Mem0 客户画像 ② Hindsight 历史教训 ③ Graphiti 领域知识（若已配置）。回答策略/方案类问题前应优先调用本工具。",
    )
    def retrieve_ordered_context(query: str) -> str:
        blocks: list[str] = []
        mem = controller.search_profile(query, client_id=client_id, user_id=user_id, limit=8)
        blocks.append("## ① 客户画像 (Mem0)\n" + ("\n---\n".join(h.text for h in mem) if mem else "（无）"))
        hs = controller.search_hindsight(query, client_id=client_id, limit=8)
        blocks.append("## ② 历史教训与反馈 (Hindsight)\n" + ("\n---\n".join(hs) if hs else "（无）"))
        if knowledge is not None:
            dom = knowledge.search_domain_knowledge(query, client_id=client_id)
            blocks.append("## ③ 领域知识 (Graphiti / 降级)\n" + dom)
        else:
            blocks.append("## ③ 领域知识 (Graphiti)\n（当前未挂载 Graphiti，依赖模型常识）")
        return "\n\n".join(blocks)

    tools: list[Callable] = [
        record_client_fact,
        record_client_preference,
        record_task_feedback,
        suggest_memory_lane_tool,
        fetch_ops_probe_context,
        retrieve_ordered_context,
        search_client_memory,
        search_past_lessons,
    ]

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
            return knowledge.search_domain_knowledge(query, client_id=client_id)

        tools.append(search_domain_knowledge)

    return filter_tools_by_manifest(tools, enabled_tool_names)
