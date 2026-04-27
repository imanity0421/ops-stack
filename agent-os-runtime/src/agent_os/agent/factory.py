from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import uuid4

from agno.agent import Agent
from agno.models.openai import OpenAIChat

from agent_os.agent.constitutional import build_constitutional_instruction_blocks
from agent_os.agent.session_db import create_session_db, session_db_summary
from agent_os.agent.skills import get_incremental_tools
from agent_os.agent.task_memory import TaskSegment, TaskSummary
from agent_os.agent.tools import build_memory_tools
from agent_os.config import Settings
from agent_os.evaluator.golden import load_golden_rules
from agent_os.handoff import load_handoff_instruction_lines
from agent_os.manifest_loader import (
    enabled_tool_name_set,
    load_skill_manifest_registry,
    resolve_effective_skill_id,
)
from agent_os.manifest_output import resolve_structured_output_model
from agent_os.knowledge.asset_store import AssetStore, asset_store_from_settings
from agent_os.memory.controller import MemoryController
from agent_os.runtime_context import (
    EntryPoint,
    build_ephemeral_context,
    build_ephemeral_instruction,
)

if TYPE_CHECKING:
    from agent_os.knowledge.graphiti_reader import GraphitiReadService

logger = logging.getLogger(__name__)

# P2-H25: 一次性记录 manifest miss，避免每轮日志噪声；进程级缓存即可。
_MANIFEST_MISS_LOGGED: set[str] = set()


def _log_manifest_miss_once(*, requested_skill_id: str | None, effective_skill_id: str) -> None:
    """显式传入 skill_id 但 registry 没有该 manifest 时，按 skill 唯一记录一次 INFO。"""
    if not requested_skill_id:
        return
    requested = str(requested_skill_id).strip()
    if not requested:
        return
    if requested in _MANIFEST_MISS_LOGGED:
        return
    _MANIFEST_MISS_LOGGED.add(requested)
    logger.info(
        "manifest miss: skill_id=%s exposes all platform tools (no enabled_tools allowlist); "
        "effective skill resolved to %s",
        requested,
        effective_skill_id,
    )


def _knowledge_status_hint(skill_id: str, knowledge: Optional["GraphitiReadService"]) -> str | None:
    if knowledge is not None:
        return None
    return "当前未挂载 Graphiti：retrieve_ordered_context 的第三层将提示未配置。"


def _model_id() -> str:
    import os

    return os.getenv("AGENT_OS_MODEL", "gpt-4o-mini")


def _model_from_manifest(manifest: object | None, settings: Settings) -> OpenAIChat:
    """创建 OpenAI 兼容客户端；必须传入 Settings 以应用 OPENAI_API_BASE（自定义网关等）。"""
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
    entrypoint: EntryPoint = "api",
    current_task_summary: TaskSummary | None = None,
    session_task_index: list[TaskSegment] | None = None,
) -> Agent:
    """
    工厂：创建 Agent。thought_mode 为 'slow' 时启用 Agno 内置 reasoning（若模型不支持会降级）。

    ``skill_id`` 主键决定 manifest 与系统级 Graphiti 分区 ``system_graphiti_group_id(skill_id)``；
    未传时使用 ``Settings.default_skill_id``（环境 ``AGENT_OS_DEFAULT_SKILL_ID``）。
    当 ``enable_context_builder`` 开启时，``entrypoint`` 只由调用方传给 ContextBuilder 的
    runtime context 使用；这里保留参数是为了 legacy instructions 路径兼容。
    """
    s = settings or Settings.from_env()
    registry = load_skill_manifest_registry(s.agent_manifest_dir)
    eff_skill = resolve_effective_skill_id(skill_id, s.default_skill_id, registry)
    manifest = registry.get(eff_skill)
    if manifest is None:
        _log_manifest_miss_once(
            requested_skill_id=skill_id, effective_skill_id=eff_skill
        )
    golden_rules = load_golden_rules(s.golden_rules_path)
    incremental = get_incremental_tools(eff_skill, settings=s)
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
        enable_temporal_grounding=s.enable_temporal_grounding,
        enable_hindsight_synthesis=s.enable_hindsight_synthesis,
        hindsight_synthesis_model=s.hindsight_synthesis_model,
        hindsight_synthesis_max_candidates=s.hindsight_synthesis_max_candidates,
        enable_asset_synthesis=s.enable_asset_synthesis,
        asset_synthesis_model=s.asset_synthesis_model,
        asset_synthesis_max_candidates=s.asset_synthesis_max_candidates,
        skill_compliance_dir=s.skill_compliance_dir,
        enable_hindsight_debug_tools=s.enable_hindsight_debug_tools,
    )

    instructions: list[str] = []
    instructions.extend(
        build_constitutional_instruction_blocks(
            manifest,
            enabled=s.enable_constitutional_prompt,
        )
    )
    if s.enable_ephemeral_metadata and not s.enable_context_builder:
        instructions.append(
            build_ephemeral_instruction(
                build_ephemeral_context(
                    timezone_name=s.runtime_timezone,
                    entrypoint=entrypoint,
                    skill_id=eff_skill,
                    client_id=client_id,
                    user_id=user_id,
                )
            )
        )
    if not s.enable_context_builder:
        from agent_os.agent.task_memory import (
            build_task_index_instruction,
            build_task_summary_instruction,
        )

        task_summary_instruction = build_task_summary_instruction(current_task_summary)
        if task_summary_instruction:
            instructions.append(task_summary_instruction)
        task_index_instruction = build_task_index_instruction(session_task_index or [])
        if task_index_instruction:
            instructions.append(task_index_instruction)
    if manifest is not None:
        sp = str(getattr(manifest, "system_prompt", "") or "").strip()
        if sp:
            instructions.append(sp)
        hv = getattr(manifest, "handbook_version", None)
        if hv and not s.enable_context_builder:
            instructions.append(f"当前配方手册版本：{hv}")
    if not s.enable_context_builder:
        instructions.extend(load_handoff_instruction_lines(s.handoff_manifest_path))
    if golden_rules and not s.enable_context_builder:
        instructions.append(
            f"已加载本地交付规则 {len(golden_rules)} 条：回复前可对关键段落调用 check_delivery_text 自检。"
        )
    hint = _knowledge_status_hint(eff_skill, knowledge)
    if hint and not s.enable_context_builder:
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

    session_db = create_session_db(s)
    if session_db is not None:
        kwargs["db"] = session_db
        n = s.session_history_max_messages
        unsafe_double_history = s.enable_context_builder and not s.context_self_managed_history
        if unsafe_double_history and not s.context_allow_agno_history_with_builder:
            logger.warning(
                "ContextBuilder is enabled while context_self_managed_history is disabled; "
                "suppressing Agno add_history_to_context to avoid double history. Set "
                "AGENT_OS_CONTEXT_ALLOW_AGNO_HISTORY_WITH_BUILDER=1 to override."
            )
        use_agno_history = not (
            s.enable_context_builder
            and (
                s.context_self_managed_history
                or (unsafe_double_history and not s.context_allow_agno_history_with_builder)
            )
        )
        if n > 0 and use_agno_history:
            kwargs["add_history_to_context"] = True
            kwargs["num_history_messages"] = n

    out_model = resolve_structured_output_model(manifest)
    if out_model is not None:
        kwargs["output_schema"] = out_model
        kwargs["structured_outputs"] = True

    logger.info(
        "get_agent skill_id=%s client_id=%s tools=%s session_db=%s",
        eff_skill,
        client_id,
        len(tools),
        "off" if session_db is None else session_db_summary(session_db),
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
    entrypoint: EntryPoint = "api",
    current_task_summary: TaskSummary | None = None,
    session_task_index: list[TaskSegment] | None = None,
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
        entrypoint=entrypoint,
        current_task_summary=current_task_summary,
        session_task_index=session_task_index,
    )


def new_session_id() -> str:
    return str(uuid4())
