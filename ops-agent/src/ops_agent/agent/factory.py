from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import uuid4

from agno.agent import Agent
from agno.models.openai import OpenAIChat

from ops_agent.agent.tools import build_memory_tools
from ops_agent.config import Settings
from ops_agent.evaluator.golden import load_golden_rules
from ops_agent.handoff import load_handoff_instruction_lines
from ops_agent.manifest_loader import enabled_tool_name_set, load_agent_manifest
from ops_agent.memory.controller import MemoryController

if TYPE_CHECKING:
    from ops_agent.knowledge.graphiti_reader import GraphitiReadService

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS_BASE: List[str] = [
    "你是面向知识付费与私域运营的专项顾问，语气专业、可执行。",
    "回答运营策略、方案、方法论前，优先调用 retrieve_ordered_context（顺序：Mem0 → Hindsight → 领域知识）。",
    "需要客户画像与偏好时也可单独使用 search_client_memory；历史教训用 search_past_lessons；仅查课程知识用 search_domain_knowledge。",
    "当用户明确表达可长期复用的信息时，使用 record_client_fact 或 record_client_preference。",
    "当用户针对本次方案/稿子给出评价时，使用 record_task_feedback。",
    "不要编造客户未提供的事实；不确定时先提问。",
]

# 短视频编导 / 脚本 demo：与 Mem0、Hindsight 工具链兼容，替换默认「私域运营」基座指令
SHORT_VIDEO_INSTRUCTIONS_BASE: List[str] = [
    "你是短视频编导与脚本顾问，擅长选题、钩子、结构、分镜与口播稿、字幕与节奏建议；语气清晰、可执行。",
    "输出脚本前若缺少关键信息（平台、时长、受众、人设/产品、禁忌与合规），先简要追问再写。",
    "交付脚本时用清晰分段（如：开场钩子 / 中段展开 / 结尾转化），并标注大致秒数或字数；需要时可给 2～3 个标题备选。",
    "写稿前优先调用 retrieve_ordered_context，按顺序参考 Mem0 人设与历史偏好、Hindsight 中对过往脚本的反馈、再查领域知识（若已配置）。",
    "用户明确长期有效的创作偏好（语气、禁忌、固定口癖）用 record_client_preference；稳定事实（账号定位、赛道、品牌名）用 record_client_fact。",
    "用户对某版脚本的评价用 record_task_feedback，便于下次复用教训。",
    "不要编造未提供的商业承诺、数据或版权素材；不确定时先提问。",
]


def _model_id() -> str:
    import os

    return os.getenv("OPS_AGENT_MODEL", "gpt-4o-mini")


def _get_model() -> OpenAIChat:
    return OpenAIChat(id=_model_id())


def _model_from_manifest(manifest: object | None, settings: Settings) -> OpenAIChat:
    """创建 OpenAI 兼容客户端；必须传入 Settings 以应用 OPENAI_API_BASE（4zapi 等中转）。"""
    mid = _model_id()
    if manifest is not None:
        m = getattr(manifest, "model", None)
        if m:
            mid = str(m)
    kwargs: dict[str, Any] = {"id": mid}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    base = (settings.openai_api_base or "").strip()
    if base:
        kwargs["base_url"] = base
    return OpenAIChat(**kwargs)


def _instruction_base_for_persona(persona: str) -> List[str]:
    p = (persona or "ops").strip().lower()
    if p == "short_video":
        return list(SHORT_VIDEO_INSTRUCTIONS_BASE)
    return list(DEFAULT_INSTRUCTIONS_BASE)


def get_agent(
    controller: MemoryController,
    *,
    client_id: str,
    user_id: str | None = None,
    thought_mode: str = "fast",
    extra_instructions: Optional[List[str]] = None,
    knowledge: Optional["GraphitiReadService"] = None,
    settings: Optional[Settings] = None,
    persona: str | None = None,
    exclude_tool_names: Optional[set[str]] = None,
) -> Agent:
    """
    工厂：创建 Agent。thought_mode 为 'slow' 时启用 Agno 内置 reasoning（若模型不支持会降级）。
    若未来 API 变更，请只改本文件。
    """
    s = settings or Settings.from_env()
    effective_persona = (persona if persona is not None else s.agent_persona) or "ops"
    if effective_persona not in ("ops", "short_video"):
        effective_persona = "ops"
    manifest = load_agent_manifest(s.agent_manifest_path)
    golden_rules = load_golden_rules(s.golden_rules_path)
    tools = build_memory_tools(
        controller,
        client_id,
        user_id,
        knowledge=knowledge,
        golden_rules=golden_rules,
        mcp_probe_fixture_path=s.mcp_probe_fixture_path,
        enabled_tool_names=enabled_tool_name_set(manifest),
        exclude_tool_names=exclude_tool_names,
    )
    instructions = _instruction_base_for_persona(effective_persona)
    if manifest is not None and getattr(manifest, "system_prompt", "") and str(manifest.system_prompt).strip():
        instructions.insert(0, str(manifest.system_prompt).strip())
    if manifest is not None and getattr(manifest, "handbook_version", None):
        instructions.append(f"当前配方手册版本：{manifest.handbook_version}")
    instructions.extend(load_handoff_instruction_lines(s.handoff_manifest_path))
    if golden_rules:
        instructions.append(
            f"已加载本地交付规则 {len(golden_rules)} 条：回复前可对关键段落调用 check_delivery_text 自检。"
        )
    if effective_persona == "short_video":
        instructions.append(
            "需要热点/竞品口径旁路时可调用 fetch_ops_probe_context（可选，默认 fixture；可换 OPS_MCP_PROBE_FIXTURE_PATH）。"
        )
    else:
        instructions.append(
            "需要时效/市场口径旁路时，可调用 fetch_ops_probe_context（默认 fixture，可换 OPS_MCP_PROBE_FIXTURE_PATH）。"
        )
    if knowledge is None:
        if effective_persona == "short_video":
            instructions.append(
                "当前未挂载领域知识库：脚本可依赖对话与 Mem0；若有固定话术库可配置 OPS_KNOWLEDGE_FALLBACK_PATH。"
            )
        else:
            instructions.append("当前未挂载 Graphiti：retrieve_ordered_context 的第三层将提示未配置。")
    if extra_instructions:
        instructions.extend(extra_instructions)

    reasoning = thought_mode.lower() in ("slow", "reasoning", "deep")

    agent_name = "ShortVideoDirector" if effective_persona == "short_video" else "OpsSpecialist"
    kwargs: dict[str, Any] = {
        "model": _model_from_manifest(manifest, s),
        "name": agent_name,
        "instructions": instructions,
        "tools": tools,
        "markdown": True,
        "reasoning": reasoning,
    }
    if reasoning:
        kwargs["reasoning_min_steps"] = 1
        kwargs["reasoning_max_steps"] = 8

    return Agent(**kwargs)


def get_reasoning_agent(
    controller: MemoryController,
    *,
    client_id: str,
    user_id: str | None = None,
    extra_instructions: Optional[List[str]] = None,
    knowledge: Optional["GraphitiReadService"] = None,
    settings: Optional[Settings] = None,
    persona: str | None = None,
    exclude_tool_names: Optional[set[str]] = None,
) -> Agent:
    """与 get_agent(..., thought_mode='slow') 等价，便于显式命名。"""
    return get_agent(
        controller,
        client_id=client_id,
        user_id=user_id,
        thought_mode="slow",
        extra_instructions=extra_instructions,
        knowledge=knowledge,
        settings=settings,
        persona=persona,
        exclude_tool_names=exclude_tool_names,
    )


def new_session_id() -> str:
    return str(uuid4())
