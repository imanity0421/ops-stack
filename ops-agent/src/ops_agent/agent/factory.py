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


def _model_id() -> str:
    import os

    return os.getenv("OPS_AGENT_MODEL", "gpt-4o-mini")


def _get_model() -> OpenAIChat:
    return OpenAIChat(id=_model_id())


def _model_from_manifest(manifest: object | None) -> OpenAIChat:
    mid = _model_id()
    if manifest is not None:
        m = getattr(manifest, "model", None)
        if m:
            mid = str(m)
    return OpenAIChat(id=mid)


def get_agent(
    controller: MemoryController,
    *,
    client_id: str,
    user_id: str | None = None,
    thought_mode: str = "fast",
    extra_instructions: Optional[List[str]] = None,
    knowledge: Optional["GraphitiReadService"] = None,
    settings: Optional[Settings] = None,
) -> Agent:
    """
    工厂：创建 Agent。thought_mode 为 'slow' 时启用 Agno 内置 reasoning（若模型不支持会降级）。
    若未来 API 变更，请只改本文件。
    """
    s = settings or Settings.from_env()
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
    )
    instructions = list(DEFAULT_INSTRUCTIONS_BASE)
    if manifest is not None and getattr(manifest, "system_prompt", "") and str(manifest.system_prompt).strip():
        instructions.insert(0, str(manifest.system_prompt).strip())
    if manifest is not None and getattr(manifest, "handbook_version", None):
        instructions.append(f"当前配方手册版本：{manifest.handbook_version}")
    instructions.extend(load_handoff_instruction_lines(s.handoff_manifest_path))
    if golden_rules:
        instructions.append(
            f"已加载本地交付规则 {len(golden_rules)} 条：回复前可对关键段落调用 check_delivery_text 自检。"
        )
    instructions.append(
        "需要时效/市场口径旁路时，可调用 fetch_ops_probe_context（默认 fixture，可换 OPS_MCP_PROBE_FIXTURE_PATH）。"
    )
    if knowledge is None:
        instructions.append("当前未挂载 Graphiti：retrieve_ordered_context 的第三层将提示未配置。")
    if extra_instructions:
        instructions.extend(extra_instructions)

    reasoning = thought_mode.lower() in ("slow", "reasoning", "deep")

    kwargs: dict[str, Any] = {
        "model": _model_from_manifest(manifest),
        "name": "OpsSpecialist",
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
    )


def new_session_id() -> str:
    return str(uuid4())
