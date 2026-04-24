from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import uuid4

from agno.agent import Agent
from agno.models.openai import OpenAIChat

from ops_agent.agent.skills import get_incremental_tools
from ops_agent.agent.tools import build_memory_tools
from ops_agent.config import Settings
from ops_agent.evaluator.golden import load_golden_rules
from ops_agent.handoff import load_handoff_instruction_lines
from ops_agent.manifest_loader import (
    enabled_tool_name_set,
    load_skill_manifest_registry,
    resolve_effective_skill_id,
)
from ops_agent.knowledge.asset_store import AssetStore, asset_store_from_settings
from ops_agent.memory.controller import MemoryController

if TYPE_CHECKING:
    from ops_agent.knowledge.graphiti_reader import GraphitiReadService

logger = logging.getLogger(__name__)


def _knowledge_status_hint(skill_id: str, knowledge: Optional["GraphitiReadService"]) -> str | None:
    if knowledge is not None:
        return None
    if skill_id == "short_video":
        return (
            "当前未挂载领域知识库：脚本可依赖对话与 Mem0；若有固定话术库可配置 OPS_KNOWLEDGE_FALLBACK_PATH。"
        )
    return "当前未挂载 Graphiti：retrieve_ordered_context 的第三层将提示未配置。"


def _model_id() -> str:
    import os

    return os.getenv("OPS_AGENT_MODEL", "gpt-4o-mini")


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


def get_agent(
    controller: MemoryController,
    *,
    client_id: str,
    user_id: str | None = None,
    thought_mode: str = "fast",
    extra_instructions: Optional[List[str]] = None,
    knowledge: Optional["GraphitiReadService"] = None,
    asset_store: Optional[AssetStore] = None,
    settings: Optional[Settings] = None,
    skill_id: str | None = None,
    exclude_tool_names: Optional[set[str]] = None,
) -> Agent:
    """
    工厂：创建 Agent。thought_mode 为 'slow' 时启用 Agno 内置 reasoning（若模型不支持会降级）。

    ``skill_id`` 主键决定 manifest 与 Graphiti 分区 ``graphiti_group_id(client_id, skill_id)``；
    未传时使用 ``Settings.default_skill_id``（环境 ``OPS_AGENT_DEFAULT_SKILL_ID``）。
    """
    s = settings or Settings.from_env()
    registry = load_skill_manifest_registry(s.agent_manifest_dir)
    eff_skill = resolve_effective_skill_id(skill_id, s.default_skill_id, registry)
    manifest = registry.get(eff_skill)
    golden_rules = load_golden_rules(s.golden_rules_path)
    incremental = get_incremental_tools(eff_skill)
    resolved_asset_store: AssetStore | None = asset_store
    if s.enable_asset_store and resolved_asset_store is None:
        resolved_asset_store = asset_store_from_settings(enable=True, path=s.asset_store_path)
    tools = build_memory_tools(
        controller,
        client_id,
        user_id,
        knowledge=knowledge,
        asset_store=resolved_asset_store,
        golden_rules=golden_rules,
        mcp_probe_fixture_path=s.mcp_probe_fixture_path,
        enabled_tool_names=enabled_tool_name_set(manifest),
        exclude_tool_names=exclude_tool_names,
        skill_id=eff_skill,
        incremental_tools=incremental,
        enable_mem0_learning=s.enable_mem0_learning,
        enable_hindsight=s.enable_hindsight,
        enable_asset_store=s.enable_asset_store,
        skill_compliance_dir=s.skill_compliance_dir,
    )

    instructions: list[str] = []
    if manifest is not None:
        sp = str(getattr(manifest, "system_prompt", "") or "").strip()
        if sp:
            instructions.append(sp)
        hv = getattr(manifest, "handbook_version", None)
        if hv:
            instructions.append(f"当前配方手册版本：{hv}")
    instructions.extend(load_handoff_instruction_lines(s.handoff_manifest_path))
    if golden_rules:
        instructions.append(
            f"已加载本地交付规则 {len(golden_rules)} 条：回复前可对关键段落调用 check_delivery_text 自检。"
        )
    hint = _knowledge_status_hint(eff_skill, knowledge)
    if hint:
        instructions.append(hint)
    if extra_instructions:
        instructions.extend(extra_instructions)

    reasoning = thought_mode.lower() in ("slow", "reasoning", "deep")

    agent_name = "Agent"
    if manifest is not None:
        an = getattr(manifest, "agent_name", None)
        if an and str(an).strip():
            agent_name = str(an).strip()
        else:
            agent_name = f"Skill_{eff_skill}"

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

    logger.info(
        "get_agent skill_id=%s client_id=%s tools=%s",
        eff_skill,
        client_id,
        len(tools),
    )
    return Agent(**kwargs)


def get_reasoning_agent(
    controller: MemoryController,
    *,
    client_id: str,
    user_id: str | None = None,
    extra_instructions: Optional[List[str]] = None,
    knowledge: Optional["GraphitiReadService"] = None,
    asset_store: Optional[AssetStore] = None,
    settings: Optional[Settings] = None,
    skill_id: str | None = None,
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
        asset_store=asset_store,
        settings=settings,
        skill_id=skill_id,
        exclude_tool_names=exclude_tool_names,
    )


def new_session_id() -> str:
    return str(uuid4())
