#!/usr/bin/env python3
# ruff: noqa: E402
"""
浏览器中试用 agent-os-runtime：FastAPI + 多轮 chat、手动记忆写入、结束对话与可选 Hindsight 复盘。

依赖（在 agent-os-runtime 目录、已 activate venv）：
  pip install fastapi "uvicorn[standard]"

运行（在 agent-os-runtime 目录，已配置 .env）：
  python examples/web_chat_fastapi.py

修改本文件或 `agent_os` 源码后须 **停止进程（Ctrl+C）再重新启动** 并刷新浏览器；未加 `--reload` 时不会自动重载。

浏览器打开：对话 `/` ，记忆 `/memory` ，**流程与 Prompt** `/debug`（当前 Agent 指令与工具，供调试）。

说明：
- **本 Web 进程内**：模型 **不会** 获得 `record_client_fact` / `record_client_preference` / `record_task_feedback` 三个工具；写入 **仅** 能通过页面按钮（或 `POST /api/memory/ingest`）。**Hindsight 的 lesson 复盘** 仅能通过「结束对话」且勾选「进行复盘」（或 `POST /api/session/end` 且 `run_review: true`）。
- 终端 CLI `python -m agent_os` 仍为完整工具集，不受影响。
- **0.5+**：三页统一 Demo 样式；**`/debug`** 展示 Agent 工作流与指令栈。**Reasoning** 默认开启（环境 `AGENT_OS_WEB_SLOW` 未设视为开）；对话页可勾选覆盖，并与记忆 API 共用 `localStorage`。身份支持 **client_id / user_id** 预设（localStorage）。
- **0.6+**：**身份增删** 仅在 **记忆管理** 页；**对话** 页仅选预设并支持 **多会话**（localStorage 按身份分桶，刷新可恢复）。**Skill**：请求体可传 **`skill_id`**；或环境 **`AGENT_OS_WEB_SKILL_ID`** / **`AGENT_OS_DEFAULT_SKILL_ID`**（与 Graphiti **`group_id`** 分区一致）。
- **会话落库 (Agno `db`)**：默认开启，路径见 ``AGENT_OS_SESSION_DB_PATH``；多机可设 ``AGENT_OS_SESSION_DB_URL`` 指向 Postgres/Redis 等。前端**每次 /chat 携带同一 `session_id`**（F5 后从 localStorage 恢复）；进程重启后 UI 可调用 ``GET /api/session/messages`` 从库中补全与模型一致的历史条数。详见 `docs/OPERATIONS.md`。
- **P2 可观测性**：``X-Request-ID`` / ``X-Correlation-ID`` 透传；``/chat`` 结束后打一条可 grep 的 **AGENT_OS_OBS** 日志（``session_id``、``model``、``tools``、``elapsed_ms``、token 粗算）。
- **P2 摄入网关**：``POST /ingest`` 显式 ``target=mem0_profile|hindsight|asset_store``，见 `docs/examples/ingest_post_samples.md`；生产前须 **BFF/网关** 鉴权与限流（本进程无鉴权）。
- **结构化输出（如 ``planning_draft``）**：``/chat`` 响应体含 ``reply_content_kind`` 与 ``structured``，便于前端区分 JSON 与纯文本（``reply`` 在结构化时为 JSON 字符串）。
"""

from __future__ import annotations

import json
import logging
import os
import time
import hmac
import hashlib
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from agent_os.agent.factory import get_agent, new_session_id
from agent_os.agent.task_memory import TaskMemoryStore, TaskSummaryService
from agent_os.config import Settings
from agent_os.context_builder import (
    ContextCharBudget,
    ContextBuilder,
    build_auto_retrieval_context,
    effective_session_history_max_messages,
    resolve_auto_retrieve_decision,
)
from agent_os.context_diagnostics import build_context_diagnostics
from agent_os.ingest_gateway import INGEST_V1_MAX_TEXT_CHARS, run_ingest_v1
from agent_os.knowledge.graphiti_entitlements import (
    EntitlementsRevisionConflictError,
    append_entitlements_audit,
    load_entitlements_file,
    update_entitlements_file,
)
from agent_os.manifest_loader import load_skill_manifest_registry, resolve_effective_skill_id
from agent_os.observability import log_agent_run_obs, log_context_management_trace
from agent_os.knowledge.asset_store import asset_store_from_settings
from agent_os.knowledge.graphiti_reader import GraphitiReadService
from agent_os.memory.controller import MemoryController
from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.models import MemoryLane, UserFact
from agent_os.review.async_review import AsyncReviewService

_web_log = logging.getLogger(__name__)

# Web 演示：从 Agent 工具列表移除三项，仅保留页面 / API 手动写入
_WEB_EXCLUDED_MEMORY_WRITE_TOOLS = frozenset(
    {"record_client_fact", "record_client_preference", "record_task_feedback"}
)
_WEB_EXTRA_INSTRUCTIONS = [
    "【Web 演示】record_client_fact / record_client_preference / record_task_feedback 已关闭，"
    "请勿在回复中假装已写入记忆。事实、偏好、任务反馈请用户通过页面「手动写入记忆」提交；"
    "复盘 lesson 仅能通过「结束对话」并勾选「进行复盘」。",
]

# ---------- 按 (client_id, user_id, use_slow, skill_key) 缓存 Agent + MemoryController ----------
_bundles: dict[tuple[str, str, bool, str], tuple[Settings, MemoryController, Any]] = {}
_no_knowledge = os.getenv("AGENT_OS_WEB_NO_KNOWLEDGE", "1").strip() in ("1", "true", "yes")
_web_skill = os.getenv("AGENT_OS_WEB_SKILL_ID") or None

_idempotency_cache_lock = threading.Lock()
_idempotency_cache: dict[str, tuple[float, str, dict[str, Any]]] = {}


def _env_slow() -> bool:
    """环境默认：未设置 AGENT_OS_WEB_SLOW 时视为开启（与「默认慢推理」一致）；显式 0/false/no 关闭。"""
    v = (os.getenv("AGENT_OS_WEB_SLOW") or "1").strip().lower()
    return v not in ("0", "false", "no")


# session_id -> (user, assistant) 轮次列表
_transcripts: dict[str, list[tuple[str, str]]] = {}


def _bundle_skill_param(skill_id: str | None) -> str | None:
    if skill_id is not None and skill_id.strip():
        return skill_id.strip()
    if _web_skill and _web_skill.strip():
        return _web_skill.strip()
    return None


def _bundle_key(
    client_id: str, user_id: str | None, use_slow: bool, skill_id: str | None
) -> tuple[str, str, bool, str]:
    sk = _bundle_skill_param(skill_id) or ""
    return (client_id, user_id or "", use_slow, sk)


def _build_stack(
    *,
    client_id: str,
    user_id: str | None,
    no_knowledge: bool,
    skill_id: str | None,
    slow: bool,
):
    settings = Settings.from_env()
    ctrl = MemoryController.create_default(
        mem0_api_key=settings.mem0_api_key,
        mem0_host=settings.mem0_host,
        local_memory_path=settings.local_memory_path,
        hindsight_path=settings.hindsight_path,
        memory_ledger_path=settings.memory_ledger_path,
        enable_hindsight=settings.enable_hindsight,
        enable_hindsight_vector_recall=settings.enable_hindsight_vector_recall,
        hindsight_vector_index_path=settings.hindsight_vector_index_path,
        hindsight_vector_score_weight=settings.hindsight_vector_score_weight,
        hindsight_vector_candidate_limit=settings.hindsight_vector_candidate_limit,
        snapshot_every_n_turns=settings.snapshot_every_n_turns,
        enable_memory_policy=settings.enable_memory_policy,
        memory_policy_mode=settings.memory_policy_mode,
    )
    knowledge = (
        None if no_knowledge else GraphitiReadService.from_env(settings.knowledge_fallback_path)
    )
    asset_store = asset_store_from_settings(
        enable=settings.enable_asset_store, path=settings.asset_store_path
    )
    # P2-H19: Web 演示提示不再走静态 instructions，避免跨 entrypoint 静态前缀漂移；
    # 当 ContextBuilder 启用时，提示通过 attention_anchor 的 <entrypoint_notice> 注入。
    legacy_extra_instructions: list[str] | None = (
        list(_WEB_EXTRA_INSTRUCTIONS) if not settings.enable_context_builder else None
    )
    agent = get_agent(
        ctrl,
        client_id=client_id,
        user_id=user_id,
        thought_mode="slow" if slow else "fast",
        knowledge=knowledge,
        asset_store=asset_store,
        settings=settings,
        skill_id=_bundle_skill_param(skill_id),
        exclude_tool_names=set(_WEB_EXCLUDED_MEMORY_WRITE_TOOLS),
        extra_instructions=legacy_extra_instructions,
        entrypoint="web",
    )
    # Keep retrieval dependencies attached to the cached bundle so /chat auto recall
    # uses the same instances as the agent tool layer.
    setattr(agent, "_agent_os_knowledge", knowledge)
    setattr(agent, "_agent_os_asset_store", asset_store)
    return settings, ctrl, agent


def _resolve_use_slow(explicit: bool | None) -> bool:
    return explicit if explicit is not None else _env_slow()


def _get_bundle_for(
    client_id: str,
    user_id: str | None,
    use_slow_reasoning: bool | None = None,
    skill_id: str | None = None,
) -> tuple[Settings, MemoryController, Any]:
    slow = _resolve_use_slow(use_slow_reasoning)
    k = _bundle_key(client_id, user_id, slow, skill_id)
    if k not in _bundles:
        _bundles[k] = _build_stack(
            client_id=client_id,
            user_id=user_id,
            no_knowledge=_no_knowledge,
            skill_id=skill_id,
            slow=slow,
        )
    return _bundles[k]


def _mem_uid(client_id: str, user_id: str | None) -> str:
    return f"{client_id}::{user_id}" if user_id else client_id


def _context_builder_from_settings(settings: Settings) -> ContextBuilder | None:
    if not settings.enable_context_builder:
        return None
    return ContextBuilder(
        timezone_name=settings.runtime_timezone,
        history_max_messages=settings.session_history_max_messages,
        include_runtime_context=settings.enable_ephemeral_metadata,
        max_tool_output_chars=settings.context_tool_output_max_chars,
        max_tool_outputs_total_chars=settings.context_tool_outputs_total_max_chars,
        context_char_budget=ContextCharBudget.from_total(settings.context_max_chars),
        enable_token_estimate=settings.context_estimate_tokens,
        hard_total_budget=settings.context_hard_budget,
        self_heal_over_budget=settings.context_self_heal_over_budget,
    )


def _task_memory_from_settings(
    settings: Settings,
) -> tuple[TaskMemoryStore | None, TaskSummaryService | None]:
    if not settings.enable_task_memory:
        return None, None
    store = TaskMemoryStore(settings.task_memory_sqlite_path)
    return (
        store,
        TaskSummaryService(
            store,
            model=settings.task_summary_model,
            max_chars=settings.task_summary_max_chars,
            min_messages=settings.task_summary_min_messages,
            every_n_messages=settings.task_summary_every_n_messages,
        ),
    )


def _effective_skill_for_context(settings: Settings, skill_id: str | None) -> str:
    return resolve_effective_skill_id(
        _bundle_skill_param(skill_id),
        settings.default_skill_id,
        load_skill_manifest_registry(settings.agent_manifest_dir),
    )


def _session_messages_for_context(agent: Any, session_id: str, max_messages: int) -> list[Any]:
    """Prefer Agno's persisted session DB so Web history survives process restarts."""
    limit = max(0, int(max_messages))
    if limit <= 0:
        return []
    getter = getattr(agent, "get_session_messages", None)
    if getattr(agent, "db", None) is not None and callable(getter):
        try:
            messages = getter(
                session_id=session_id.strip(),
                limit=limit,
                skip_history_messages=False,
            )
            return list(messages)
        except Exception:
            pass
    return list(_transcripts.get(session_id, []))[-limit:]


def _load_local_memory_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"users": {}}
    if not isinstance(data, dict):
        return {"users": {}}
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    return data


def _resolve_under_agent_os(p: Path) -> Path:
    """Settings 里相对路径相对 agent-os-runtime 根目录。"""
    if p.is_absolute():
        return p
    return (_ROOT / p).resolve()


def _graphiti_entitlements_path() -> Path:
    raw = os.getenv(
        "AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", "data/graphiti_entitlements.json"
    ).strip()
    return _resolve_under_agent_os(Path(raw))


def _web_admin_enabled() -> bool:
    return os.getenv("AGENT_OS_WEB_ENABLE_ADMIN_API", "0").strip().lower() in ("1", "true", "yes")


def _web_admin_allowed_hosts() -> set[str]:
    raw = os.getenv("AGENT_OS_WEB_ADMIN_ALLOWED_HOSTS", "127.0.0.1,::1,localhost")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _web_admin_tokens() -> set[str]:
    raw = os.getenv("AGENT_OS_WEB_ADMIN_API_TOKENS", os.getenv("AGENT_OS_WEB_ADMIN_API_TOKEN", ""))
    return {x.strip() for x in raw.split(",") if x.strip()}


def _web_admin_idempotency_enabled() -> bool:
    return os.getenv("AGENT_OS_WEB_ADMIN_IDEMPOTENCY_ENABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _web_admin_idempotency_ttl_sec() -> float:
    raw = (os.getenv("AGENT_OS_WEB_ADMIN_IDEMPOTENCY_TTL_SEC") or "").strip()
    if not raw:
        return 600.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 600.0


def _assert_admin_request_allowed(request: Request) -> None:
    if not _web_admin_enabled():
        raise HTTPException(403, detail="未启用管理接口（AGENT_OS_WEB_ENABLE_ADMIN_API=0）")
    host = (getattr(request.client, "host", None) or "").strip()
    if host not in _web_admin_allowed_hosts():
        raise HTTPException(403, detail="仅允许本机访问该管理接口")
    tokens = _web_admin_tokens()
    if not tokens:
        raise HTTPException(403, detail="未配置管理接口 token（AGENT_OS_WEB_ADMIN_API_TOKEN(S)）")

    raw = (request.headers.get("x-admin-token") or "").strip() or (
        request.headers.get("X-Admin-Token") or ""
    ).strip()
    auth = (request.headers.get("authorization") or "").strip()
    if not raw and auth.lower().startswith("bearer "):
        raw = auth[7:].strip()
    if not raw:
        raise HTTPException(
            401, detail="缺少管理接口 token（x-admin-token 或 Authorization: Bearer）"
        )

    if not any(hmac.compare_digest(raw, expected) for expected in tokens):
        raise HTTPException(403, detail="管理接口 token 无效")


def _idempotency_key_from_request(request: Request) -> str | None:
    raw = (request.headers.get("idempotency-key") or "").strip() or (
        request.headers.get("Idempotency-Key") or ""
    ).strip()
    return raw or None


def _idempotency_request_hash(payload: dict[str, Any]) -> str:
    s = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _idempotency_cache_scope(request: Request, key: str) -> str:
    return f"{request.method}:{request.url.path}:{key}"


def _idempotency_cache_check(
    request: Request,
    *,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not _web_admin_idempotency_enabled():
        return None
    key = _idempotency_key_from_request(request)
    if not key:
        return None
    scope = _idempotency_cache_scope(request, key)
    req_hash = _idempotency_request_hash(payload)
    now = time.time()
    with _idempotency_cache_lock:
        expired = [k for k, (exp, _h, _r) in _idempotency_cache.items() if exp <= now]
        for k in expired:
            _idempotency_cache.pop(k, None)
        hit = _idempotency_cache.get(scope)
        if hit is None:
            return None
        exp, prev_hash, resp = hit
        if exp <= now:
            _idempotency_cache.pop(scope, None)
            return None
        if prev_hash != req_hash:
            raise HTTPException(
                409,
                detail={
                    "code": "idempotency_key_reused",
                    "message": "同一 Idempotency-Key 不可复用于不同请求体",
                },
            )
        return resp


def _idempotency_cache_store(
    request: Request,
    *,
    payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> None:
    if not _web_admin_idempotency_enabled():
        return
    key = _idempotency_key_from_request(request)
    if not key:
        return
    ttl = _web_admin_idempotency_ttl_sec()
    scope = _idempotency_cache_scope(request, key)
    req_hash = _idempotency_request_hash(payload)
    copied = json.loads(json.dumps(response_payload, ensure_ascii=False))
    with _idempotency_cache_lock:
        _idempotency_cache[scope] = (time.time() + ttl, req_hash, copied)


def _admin_actor(request: Request) -> str:
    return (
        (request.headers.get("x-admin-actor") or "").strip()
        or (request.headers.get("X-Admin-Actor") or "").strip()
        or "web_admin"
    )


def _tool_display_name(fn: Any) -> str:
    return str(getattr(fn, "name", None) or getattr(fn, "__name__", "") or type(fn).__name__)


def _normalize_agent_instructions(agent: Any) -> list[str]:
    inst = getattr(agent, "instructions", None)
    if inst is None:
        return []
    if isinstance(inst, str):
        return [inst]
    if isinstance(inst, list):
        return [str(x) for x in inst]
    return [str(inst)]


def _agent_inspect_payload(
    client_id: str,
    user_id: str | None,
    use_slow_reasoning: bool | None = None,
    skill_id: str | None = None,
) -> dict[str, Any]:
    """当前 Web 进程内、与对话一致的 Agent 实例：指令栈、工具名、Manifest 与路径（供 /debug 调试）。"""
    slow_applied = _resolve_use_slow(use_slow_reasoning)
    settings, _ctrl, agent = _get_bundle_for(client_id, user_id, slow_applied, skill_id=skill_id)
    reg = load_skill_manifest_registry(settings.agent_manifest_dir)
    eff = resolve_effective_skill_id(_bundle_skill_param(skill_id), settings.default_skill_id, reg)
    manifest = reg.get(eff)
    model = getattr(agent, "model", None)
    model_id = getattr(model, "id", None) or getattr(model, "name", None) or str(model)
    tools_raw = getattr(agent, "tools", None) or []
    tool_names = [_tool_display_name(t) for t in tools_raw]
    return {
        "agent_name": getattr(agent, "name", None),
        "model_id": model_id,
        "reasoning": getattr(agent, "reasoning", None),
        "reasoning_min_steps": getattr(agent, "reasoning_min_steps", None),
        "reasoning_max_steps": getattr(agent, "reasoning_max_steps", None),
        "markdown": getattr(agent, "markdown", None),
        "instructions": _normalize_agent_instructions(agent),
        "tools": tool_names,
        "manifest": manifest.model_dump() if manifest else None,
        "paths": {
            "agent_manifest_dir": str(settings.agent_manifest_dir)
            if settings.agent_manifest_dir
            else None,
            "handoff": str(settings.handoff_manifest_path)
            if settings.handoff_manifest_path
            else None,
            "golden_rules": str(settings.golden_rules_path) if settings.golden_rules_path else None,
            "knowledge_fallback": str(settings.knowledge_fallback_path)
            if settings.knowledge_fallback_path
            else None,
            "local_memory": str(_resolve_under_agent_os(settings.local_memory_path)),
            "hindsight": str(_resolve_under_agent_os(settings.hindsight_path)),
            "session_persistence": {
                "enable": settings.enable_session_db,
                "has_url": bool(settings.session_db_url),
                "sqlite_path": str(_resolve_under_agent_os(settings.session_sqlite_path))
                if not settings.session_db_url
                else None,
                "history_max_messages": settings.session_history_max_messages,
            },
        },
        "skill_id_resolved": eff,
        "use_slow_reasoning_applied": slow_applied,
        "env_slow_default": _env_slow(),
        "env_flags": {
            "AGENT_OS_WEB_NO_KNOWLEDGE": _no_knowledge,
            "AGENT_OS_WEB_SLOW_env_parsed_default": _env_slow(),
            "AGENT_OS_WEB_SKILL_ID": _web_skill,
            "AGENT_OS_DEFAULT_SKILL_ID": settings.default_skill_id,
        },
        "web_excluded_tools": sorted(_WEB_EXCLUDED_MEMORY_WRITE_TOOLS),
    }


def _serialize_run_trace(out: Any) -> dict[str, Any]:
    """从 Agno RunOutput 提取可 JSON 化的执行/思考摘要。"""
    trace: dict[str, Any] = {}
    rc = getattr(out, "reasoning_content", None)
    if rc:
        trace["reasoning_content"] = rc
    rsteps = getattr(out, "reasoning_steps", None)
    if rsteps:
        trace["reasoning_steps"] = [
            s.model_dump() if hasattr(s, "model_dump") else str(s) for s in rsteps
        ]
    tools = getattr(out, "tools", None)
    if tools:
        trace["tools"] = []
        for t in tools:
            if hasattr(t, "to_dict"):
                trace["tools"].append(t.to_dict())
            else:
                trace["tools"].append(str(t))
    metrics = getattr(out, "metrics", None)
    if metrics is not None and hasattr(metrics, "to_dict"):
        trace["metrics"] = metrics.to_dict()
    ev = getattr(out, "events", None)
    if ev:
        trace["events"] = []
        for e in ev[:40]:
            if hasattr(e, "to_dict"):
                trace["events"].append(e.to_dict())
            else:
                trace["events"].append(str(e))
        if len(ev) > 40:
            trace["events_truncated"] = len(ev) - 40
    return trace


def _format_web_chat_reply(
    agent: Any, out: Any
) -> tuple[str, Literal["text", "structured_json"], dict[str, Any] | None]:
    """当 Agent 挂载 ``output_schema``（如 ``planning_draft``）时，将 Pydantic/dict 转为 JSON 字符串 + 附带 ``structured``。"""
    if getattr(agent, "output_schema", None) is None:
        c = out.content
        text = c if isinstance(c, str) else str(c)
        return (text, "text", None)
    c = out.content
    if c is None:
        return ("", "structured_json", None)
    if hasattr(c, "model_dump"):
        d = c.model_dump()
        return (json.dumps(d, ensure_ascii=False), "structured_json", d)
    if isinstance(c, dict):
        return (json.dumps(c, ensure_ascii=False), "structured_json", c)
    text = c if isinstance(c, str) else str(c)
    return (text, "text", None)


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=INGEST_V1_MAX_TEXT_CHARS)
    session_id: str | None = None
    client_id: str = Field(default="demo_client", min_length=1, max_length=256)
    user_id: str | None = None
    skill_id: str | None = Field(
        default=None,
        description="与 AGENT_OS_WEB_SKILL_ID / 默认 skill 一致；未传则按环境与会话策略解析",
    )
    use_slow_reasoning: bool | None = Field(
        default=None,
        description="是否启用 Agno 慢推理（reasoning）；None 表示采用环境默认（AGENT_OS_WEB_SLOW，默认开启）",
    )
    include_trace: bool = Field(
        default=True,
        description="是否在响应中带出 reasoning / 工具调用等执行过程（Agno RunOutput）",
    )


class ChatHistoryTurn(BaseModel):
    """当前 session 内已累计的多轮对话（与后端 _transcripts 一致）。"""

    role: Literal["user", "assistant"]
    content: str


class ChatOut(BaseModel):
    reply: str
    session_id: str
    use_slow_reasoning_applied: bool = Field(
        description="本请求实际使用的慢推理开关（与缓存 Agent 一致）",
    )
    trace: dict[str, Any] | None = None
    history: list[ChatHistoryTurn] = Field(
        default_factory=list,
        description="本 session 至今全部轮次，便于前端像聊天应用一样展示",
    )
    reply_content_kind: Literal["text", "structured_json"] = Field(
        default="text",
        description="text：普通文本；structured_json：Agno output_schema 返回（如 planning_draft），见 structured",
    )
    structured: dict[str, Any] | None = Field(
        default=None,
        description="当 reply_content_kind=structured_json 时，与 model 输出等价的 dict（可 JSON 序列化展示）",
    )


class MemoryIngestIn(BaseModel):
    """与 record_client_fact / record_client_preference / record_task_feedback 等价写入。"""

    client_id: str = Field(default="demo_client", min_length=1)
    user_id: str | None = None
    skill_id: str | None = Field(
        default=None, description="与对话 skill 对齐，用于选取同一 Agent bundle"
    )
    use_slow_reasoning: bool | None = Field(default=None, description="选取与对话一致的 bundle")
    text: str = Field(..., min_length=1, max_length=INGEST_V1_MAX_TEXT_CHARS)
    kind: str = Field(..., description="fact | preference | feedback")
    task_id: str | None = None


class MemoryIngestOut(BaseModel):
    status: str
    written_to: list[str] = Field(default_factory=list)
    dedup_skipped: bool = False
    detail: str | None = None


class SessionEndIn(BaseModel):
    session_id: str = Field(..., min_length=1)
    client_id: str = Field(default="demo_client", min_length=1)
    user_id: str | None = None
    skill_id: str | None = Field(default=None, description="与对话 skill 对齐")
    use_slow_reasoning: bool | None = Field(
        default=None,
        description="与对话请求一致，用于选取同一 MemoryController（复盘写入等）",
    )
    task_id: str | None = None
    run_review: bool = False


class SessionEndOut(BaseModel):
    status: str
    review: str | None = None
    transcript_turns: int = 0


class ProfileDeleteLocalIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    user_id: str | None = None
    skill_id: str | None = Field(default=None, description="与对话 skill 对齐")
    use_slow_reasoning: bool | None = Field(
        default=None, description="选取与对话一致的 bundle（默认随环境）"
    )
    index: int = Field(..., ge=0, description="local_memory.json 内该用户 memories 数组下标")


class HindsightDeleteIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    skill_id: str | None = Field(default=None, description="与对话 skill 对齐")
    use_slow_reasoning: bool | None = Field(default=None, description="选取与对话一致的 bundle")
    file_line: int = Field(..., ge=1, description="hindsight.jsonl 中的物理行号（自列表接口返回）")


class IngestV1In(BaseModel):
    """P2-6 显式 target 数据摄入；与旧版 ``/api/memory/ingest`` 并存。"""

    target: Literal["mem0_profile", "hindsight", "asset_store"]
    text: str = Field(
        ..., min_length=1, max_length=INGEST_V1_MAX_TEXT_CHARS, description="写入正文"
    )
    client_id: str = Field(default="demo_client", min_length=1)
    user_id: str | None = None
    skill_id: str | None = Field(
        default=None,
        description="asset_store 时建议显式；其它 target 可省略（用默认 skill）",
    )
    mem_kind: str | None = Field(
        default=None,
        description="仅 target=mem0_profile：fact | preference，默认 fact",
    )
    task_id: str | None = Field(
        default=None, description="仅 target=hindsight：与反馈关联的任务 id"
    )
    supersedes_event_id: str | None = Field(
        default=None,
        description="仅 target=hindsight：被取代的 Hindsight event_id（须为本租户既有行）",
    )
    weight_count: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="仅 target=hindsight：写入权重，默认 1",
    )
    use_slow_reasoning: bool | None = Field(
        default=None,
        description="与 ``/api/memory/ingest`` 一致，用于复用同一 MemoryController bundle",
    )


class GraphitiEntitlementsUpsertIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    skills: list[str] = Field(default_factory=list, description="可访问 skill 列表；可包含 *")
    expected_revision: int | None = Field(
        default=None,
        ge=0,
        description="乐观并发控制：仅当当前 revision 匹配时才写入",
    )


class GraphitiEntitlementsGlobalIn(BaseModel):
    skills: list[str] = Field(default_factory=list, description="全局可访问 skill 列表；可包含 *")
    expected_revision: int | None = Field(
        default=None,
        ge=0,
        description="乐观并发控制：仅当当前 revision 匹配时才写入",
    )


class _RequestIdMiddleware(BaseHTTPMiddleware):
    """P2-5：``X-Request-ID`` 或 ``X-Correlation-ID`` 透传，缺失则生成 UUID。"""

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        h = request.headers
        rid = (h.get("x-request-id") or h.get("X-Request-ID") or "").strip()
        if not rid:
            rid = (h.get("x-correlation-id") or h.get("X-Correlation-ID") or "").strip()
        if not rid:
            rid = str(uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


app = FastAPI(title="agent-os-runtime web demo", version="0.6.1")
app.add_middleware(_RequestIdMiddleware)


def _nav_html(active: str) -> str:
    """active: 'chat' | 'memory' | 'debug'"""

    def _a(href: str, label: str, key: str) -> str:
        st = ' style="font-weight:600;color:#0a58ca"' if active == key else ""
        return f'<a href="{href}"{st}>{label}</a>'

    return f"""<nav class="topnav">
  {_a("/", "对话", "chat")}
  <span class="sep">|</span>
  {_a("/memory", "记忆管理", "memory")}
  <span class="sep">|</span>
  {_a("/debug", "流程与 Prompt", "debug")}
</nav>"""


def _identity_block_memory(
    default_cid: str,
    *,
    page_note: str,
    open_default: bool = False,
    summary_title: str = "身份与快捷预设",
) -> str:
    """记忆管理页：可编辑 client_id/user_id，并增删预设。"""
    open_attr = " open" if open_default else ""
    return f"""
<details class="identity-fold"{open_attr}>
<summary class="identity-sum">
  <span class="identity-sum-title">{summary_title}</span>
  <span class="identity-preview" id="identityPreview">{default_cid}</span>
</summary>
<div class="identity-inner">
  <p class="identity-hint">{page_note}</p>
  <p id="identityFeedback" class="identity-feedback" aria-live="polite"></p>

  <div class="identity-section-title">client_id / user_id（租户键）</div>
  <div class="identity-grid">
    <label>client_id<input id="cid" type="text" value="{default_cid}" autocomplete="off" spellcheck="false"/></label>
    <label>user_id<input id="uid" type="text" placeholder="可选" autocomplete="off" spellcheck="false"/></label>
  </div>
  <button type="button" class="btn-sm btn-secondary" id="btnApplyManual" title="确认输入框中的身份并刷新本页列表（不影对话页会话）">应用身份</button>

  <div class="identity-section-title">身份预设（对话页下拉来源）</div>
  <p class="identity-hint" style="margin-top:0">下拉变更会切换身份；「保存为预设」保存当前输入框；对话页只能<strong>选择</strong>此处维护的预设。</p>
  <div class="identity-toolbar">
    <label class="lbl-preset">预设 <select id="identityPresetSel" class="sel-compact" data-role="identity-preset" title="选择一条预设"></select></label>
    <button type="button" class="btn-sm" id="btnApplyPreset" title="再次套用所选预设到输入框">再应用所选</button>
    <button type="button" class="btn-sm" id="btnSavePreset" title="将当前输入框存为新预设">保存为预设</button>
    <button type="button" class="btn-sm" id="btnDelPreset" title="删除所选预设">删除所选</button>
  </div>
  <div id="presetOutline" class="preset-outline"><strong>已保存预设一览</strong><ul id="presetOutlineList"></ul></div>
</div>
</details>
"""


def _identity_block_chat(default_cid: str, *, show_session_panel: bool = True) -> str:
    """对话页：仅选预设 + 隐藏 cid/uid；可选多会话列表。"""
    sess = ""
    if show_session_panel:
        sess = """
  <div class="identity-section-title">本机对话</div>
  <p class="identity-hint identity-hint--tight">与身份绑定，存于浏览器；点「管理」可批量切换或删除。</p>
  <div class="session-toolbar session-toolbar--compact">
    <label class="lbl-preset lbl-session">当前 <select id="sessionSel" class="sel-compact session-sel" title="切换对话"></select></label>
    <button type="button" class="btn-sm" id="btnNewChat" title="新建对话">＋ 新建</button>
    <button type="button" class="btn-sm" id="btnManageSessions" title="管理全部对话">管理…</button>
  </div>
  <div class="identity-session-row identity-session-row--compact">
    <span class="sid-label">Session</span>
    <input id="sid" class="sid-chip" type="text" readonly placeholder="发送后显示" title="session_id"/>
    <button type="button" class="btn-sm" id="btnCopySid">复制</button>
  </div>

  <dialog id="sessionDialog" class="session-dialog">
    <div class="session-dialog-inner">
      <div class="session-dialog-head">
        <h3 class="session-dialog-title">管理对话</h3>
        <button type="button" class="btn-icon" id="btnCloseSessionDialog" aria-label="关闭">×</button>
      </div>
      <p class="identity-hint identity-hint--tight">点击一行切换当前对话；删除仅移除本地记录。</p>
      <ul id="sessionManageUl" class="session-manage-list"></ul>
      <div class="session-dialog-foot">
        <button type="button" class="btn-primary btn-sm" id="btnNewChatDialog">＋ 新建对话</button>
      </div>
    </div>
  </dialog>
"""
    return f"""
<details class="identity-fold" open>
<summary class="identity-sum">
  <span class="identity-sum-title">身份与对话</span>
  <span class="identity-preview" id="identityPreview">{default_cid}</span>
</summary>
<div class="identity-inner">
  <p class="identity-hint">选择<strong>身份预设</strong>决定使用哪套记忆。增删预设请到 <a href="/memory">记忆管理</a>。</p>
  <p id="identityFeedback" class="identity-feedback" aria-live="polite"></p>
  <input type="hidden" id="cid" value="{default_cid}"/>
  <input type="hidden" id="uid" value=""/>
  <div class="identity-toolbar">
    <label class="lbl-preset">身份预设 <select id="identityPresetSel" class="sel-compact" data-role="identity-preset"></select></label>
  </div>
  {sess}
</details>
"""


# 对话页与记忆页共用：折叠身份区（避免两处样式漂移）
_IDENTITY_CSS = """
.identity-fold{border:1px solid var(--border);border-radius:var(--radius-panel);background:var(--surface);margin:0 0 var(--section-gap);padding:0;}
.identity-fold>summary.identity-sum{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:0.5rem;padding:0.45rem 0.65rem;font-size:var(--text-sm);font-weight:600;color:#475569;user-select:none;}
.identity-fold>summary::-webkit-details-marker{display:none;}
.identity-sum-title::before{content:"▸ ";display:inline-block;transition:transform .15s;}
details.identity-fold[open] .identity-sum-title::before{transform:rotate(90deg);}
.identity-preview{font-family:ui-monospace,monospace;font-weight:400;font-size:var(--text-xs);color:var(--muted);max-width:55%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.identity-inner{padding:0.4rem 0.65rem 0.55rem;border-top:1px solid #f1f5f9;}
.identity-hint{font-size:var(--text-xs);color:var(--muted);margin:0.25rem 0;line-height:1.45;}
.identity-hint--tight{margin-top:0;margin-bottom:0.2rem;}
.identity-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:0.4rem 0.5rem;margin-bottom:0.25rem;}
.lbl-preset{font-size:var(--text-sm);color:#475569;display:flex;align-items:center;gap:0.35rem;}
.lbl-session{flex:1;min-width:0;align-items:stretch;}
.sel-compact{min-width:120px;max-width:min(52vw,280px);font-size:var(--text-sm);}
.session-sel{flex:1;min-width:min(100%,160px);max-width:none;width:auto;}
.identity-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.35rem 0.65rem;margin-bottom:0.35rem;}
@media (max-width:520px){.identity-grid{grid-template-columns:1fr;}}
.identity-grid label{display:flex;flex-direction:column;gap:0.12rem;font-size:var(--text-xs);color:#475569;font-weight:500;}
.identity-grid input{font-size:var(--text-sm);}
.btn-sm{padding:0.28rem 0.55rem;font-size:var(--text-xs);border-radius:6px;border:1px solid #cbd5e1;background:#fff;}
.btn-sm:hover:not(:disabled){background:#f1f5f9;}
.btn-secondary{width:100%;margin-top:0.15rem;color:#475569;}
.identity-feedback{min-height:1.1rem;font-size:var(--text-xs);margin:0.15rem 0 0;line-height:1.35;color:#059669;}
.identity-section-title{font-size:0.6875rem;font-weight:700;color:#64748b;margin:0.45rem 0 0.2rem;text-transform:uppercase;letter-spacing:0.06em;}
.identity-session-row{display:flex;flex-wrap:wrap;align-items:center;gap:0.4rem;margin:0.3rem 0 0;}
.identity-session-row--compact{margin-top:0.4rem;}
.identity-session-row .sid-chip{flex:1;min-width:min(100%,160px);}
.sid-label{font-size:var(--text-xs);color:var(--muted);font-weight:600;flex-shrink:0;}
.preset-outline{font-size:var(--text-xs);color:#475569;margin:0.4rem 0 0;padding:0.45rem 0.55rem;background:#f8fafc;border:none;border-radius:var(--radius-panel);max-height:8rem;overflow:auto;}
.preset-outline ul{margin:0;padding-left:1.1rem;}
.preset-outline li{margin:0.2rem 0;}
.session-toolbar{margin:0;}
.session-toolbar--compact{display:flex;flex-wrap:wrap;align-items:center;gap:0.45rem;margin:0.3rem 0 0;}
.session-dialog{border:none;border-radius:12px;padding:0;max-width:min(94vw,440px);background:#fff;box-shadow:0 25px 50px -12px rgba(15,23,42,.28);}
.session-dialog::backdrop{background:rgba(15,23,42,.38);}
.session-dialog-inner{padding:0 0 0.5rem;}
.session-dialog-head{display:flex;align-items:center;justify-content:space-between;gap:0.5rem;padding:0.65rem 0.75rem 0.45rem;border-bottom:1px solid var(--border);}
.session-dialog-title{font-size:1rem;font-weight:700;margin:0;color:#1e293b;letter-spacing:-0.02em;}
.btn-icon{font-size:1.35rem;line-height:1;width:2rem;height:2rem;border:none;background:transparent;color:#64748b;border-radius:8px;cursor:pointer;display:flex;align-items:center;justify-content:center;}
.btn-icon:hover{background:#f1f5f9;color:#334155;}
.session-dialog .identity-hint{padding:0.35rem 0.75rem 0;}
.session-manage-list{list-style:none;margin:0.4rem 0.75rem 0;padding:0;font-size:var(--text-sm);max-height:min(48vh,340px);overflow:auto;border:1px solid var(--border);border-radius:var(--radius-panel);background:#fff;}
.session-manage-list li{padding:0.5rem 0.6rem;border-bottom:1px solid #f1f5f9;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:0.35rem;flex-wrap:wrap;}
.session-manage-list li:last-child{border-bottom:none;}
.session-manage-list li.active{background:#eff6ff;}
.session-manage-list .sess-title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:var(--text-sm);color:#334155;}
.session-manage-list .sess-meta{font-size:var(--text-xs);color:var(--muted);font-family:ui-monospace,monospace;}
.session-manage-list .sess-del{font-size:var(--text-xs);color:#b91c1c;cursor:pointer;border:none;background:transparent;padding:0.2rem 0.35rem;border-radius:4px;}
.session-manage-list .sess-del:hover{background:#fef2f2;text-decoration:none;}
.session-dialog-foot{padding:0.55rem 0.75rem 0.65rem;display:flex;justify-content:flex-start;}
"""

# 全站 Demo：与「消息」面板同一视觉层级（panel + 顶栏 + 表格）
_DEMO_BASE_CSS = """
:root{--border:#e2e8f0;--muted:#64748b;--accent:#2563eb;--surface:#f8fafc;--field-border:#cbd5e1;--field-radius:8px;--field-pad:0.55rem 0.65rem;--field-bg:#fff;--field-inset:inset 0 1px 2px rgba(15,23,42,.04);--section-gap:0.75rem;--radius-panel:10px;--text-xs:0.75rem;--text-sm:0.8125rem;}
body{font-family:system-ui,-apple-system,sans-serif;max-width:880px;margin:0 auto;padding:0.65rem 1rem 1.75rem;line-height:1.5;color:#1e293b;background:#fff;font-size:var(--text-sm);}
textarea,input[type=text],select{box-sizing:border-box;font:inherit;color:inherit;padding:var(--field-pad);border:1px solid var(--field-border);border-radius:var(--field-radius);line-height:1.45;background:var(--field-bg);box-shadow:var(--field-inset);}
textarea,input[type=text]{width:100%;max-width:100%;}
textarea{min-height:72px;}
select{cursor:pointer;}
textarea::placeholder,input[type=text]::placeholder{color:#94a3b8;}
button{font:inherit;cursor:pointer;border-radius:6px;border:1px solid #cbd5e1;background:#fff;}
button:disabled{opacity:0.55;cursor:not-allowed;}
button:focus-visible,summary:focus-visible,textarea:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.page-head{display:flex;align-items:baseline;justify-content:space-between;gap:0.75rem;flex-wrap:wrap;margin:0 0 0.15rem;}
.page-title{font-size:1.28rem;font-weight:700;margin:0 0 var(--section-gap);letter-spacing:-0.02em;color:#0f172a;}
.topnav{font-size:var(--text-sm);padding:0.3rem 0;margin-bottom:0.35rem;border-bottom:1px solid var(--border);}
.topnav a{text-decoration:none;color:#334155;}
.topnav a:hover{color:var(--accent);}
.topnav .sep{margin:0 0.45rem;color:#94a3b8;}
.hint{font-size:var(--text-sm);color:#475569;margin:0 0 var(--section-gap);line-height:1.45;max-width:62ch;}
.hint a{color:var(--accent);}
.row{display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center;margin:0.35rem 0;}
label.chk{display:inline-flex;align-items:center;gap:0.35rem;font-size:0.88rem;}
.demo-panel{border:1px solid #bfdbfe;background:linear-gradient(180deg,#f0f9ff 0%,#fff 14%);padding:0.75rem 0.9rem;margin:var(--section-gap) 0;border-radius:var(--radius-panel);}
.demo-panel > legend{font-size:0.95rem;font-weight:700;padding:0 0.3rem;color:#1e3a5f;}
.demo-panel--emerald{border-color:#a7f3d0;background:linear-gradient(180deg,#ecfdf5 0%,#fff 14%);}
.demo-panel--emerald > legend{color:#065f46;}
.demo-panel--violet{border-color:#ddd6fe;background:linear-gradient(180deg,#f5f3ff 0%,#fff 14%);}
.demo-panel--violet > legend{color:#5b21b6;}
.demo-panel--amber{border-color:#fde68a;background:linear-gradient(180deg,#fffbeb 0%,#fff 14%);}
.demo-panel--amber > legend{color:#92400e;}
.demo-panel--slate{border-color:#cbd5e1;background:linear-gradient(180deg,#f1f5f9 0%,#fff 14%);}
.demo-panel--slate > legend{color:#334155;}
.mem-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;margin:0.35rem 0;}
.btn-primary{padding:0.45rem 0.9rem;font-weight:600;background:var(--accent);color:#fff;border-color:#1d4ed8;}
.btn-primary:hover:not(:disabled){filter:brightness(1.05);}
.btn-ghost{padding:0.35rem 0.65rem;background:#fff;}
.in-block{margin:0.5rem 0 0;font-size:0.88rem;font-weight:600;color:#334155;}
#memOut,#memList,#hindList{white-space:pre-wrap;border:1px solid var(--border);padding:0.75rem;margin-top:0.45rem;background:#fff;font-size:0.85rem;max-height:380px;overflow:auto;border-radius:8px;}
table.memtbl{width:100%;border-collapse:collapse;font-size:0.82rem;}
.memtbl td,.memtbl th{border:1px solid var(--border);padding:0.4rem;vertical-align:top;}
.memtbl th{background:#f1f5f9;text-align:left;}
.code-block{font-family:ui-monospace,monospace;font-size:0.78rem;white-space:pre-wrap;border:1px solid var(--border);background:#fff;padding:0.65rem;border-radius:8px;max-height:min(48vh,400px);overflow:auto;line-height:1.45;}
.flow-wrap{margin:0.65rem 0;}
.flow-title{font-size:0.72rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin:0 0 0.4rem;}
.flow-col{display:flex;flex-direction:column;gap:0.45rem;}
.flow-row{display:flex;flex-wrap:wrap;align-items:stretch;gap:0.4rem;}
.flow-node{font-size:0.82rem;padding:0.5rem 0.7rem;border-radius:8px;border:1px solid #cbd5e1;background:var(--surface);flex:1;min-width:140px;}
.flow-node strong{display:block;font-size:0.7rem;color:var(--muted);margin-bottom:0.25rem;text-transform:uppercase;letter-spacing:0.04em;}
.flow-node--hi{border-color:#93c5fd;background:#eff6ff;}
.flow-node--ok{border-color:#86efac;background:#f0fdf4;}
.flow-arrow{align-self:center;color:var(--muted);font-size:1rem;padding:0 0.15rem;}
.meta-dl{display:grid;grid-template-columns:minmax(7rem,auto) 1fr;gap:0.35rem 0.75rem;font-size:0.82rem;margin:0.5rem 0;align-items:start;}
.meta-dl dt{color:var(--muted);font-weight:600;margin:0;}
.meta-dl dd{margin:0;font-family:ui-monospace,monospace;font-size:0.76rem;word-break:break-all;}
.instr-ol{margin:0.35rem 0 0;padding-left:1.15rem;font-size:0.84rem;line-height:1.55;}
.instr-ol li{margin:0.4rem 0;}
.tool-tags{display:flex;flex-wrap:wrap;gap:0.35rem;margin:0.35rem 0 0;}
.tool-tag{font-family:ui-monospace,monospace;font-size:0.76rem;padding:0.2rem 0.45rem;border-radius:5px;background:#f1f5f9;border:1px solid var(--border);}
.tool-tag--off{opacity:0.55;text-decoration:line-through;}
"""

_CHAT_EXTRA_CSS = """
#thread{border:1px solid var(--border);border-radius:var(--radius-panel);padding:0.65rem 0.75rem;margin:0.4rem 0 0;background:#fff;min-height:min(26vh,180px);max-height:min(56vh,520px);overflow:auto;display:flex;flex-direction:column;gap:0.55rem;box-shadow:var(--field-inset);}
.msg{max-width:94%;padding:0.45rem 0.65rem;border-radius:10px;font-size:var(--text-sm);line-height:1.5;}
.msg.user{align-self:flex-end;background:#dbeafe;border:1px solid #93c5fd;}
.msg.assistant{align-self:flex-start;background:#f1f5f9;border:1px solid #e2e8f0;}
.msg .who{font-size:var(--text-xs);color:var(--muted);margin-bottom:0.15rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;}
.msg .body{white-space:pre-wrap;word-break:break-word;}
.chat-meta{display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;margin:0 0 0.5rem;font-size:var(--text-xs);color:var(--muted);}
.chat-meta .meta-label{font-weight:600;color:#64748b;}
.sid-chip{font-family:ui-monospace,monospace;font-size:var(--text-xs);flex:1;min-width:0;}
.composer{display:flex;gap:0.5rem;align-items:flex-end;margin-top:0.4rem;}
.composer textarea{flex:1;min-height:76px;max-height:200px;resize:vertical;}
.btn-send{padding:0.5rem 1rem;font-weight:600;background:var(--accent);color:#fff;border-color:#1d4ed8;white-space:nowrap;align-self:stretch;display:flex;align-items:center;justify-content:center;min-width:4.75rem;border-radius:var(--field-radius);}
.btn-send:hover:not(:disabled){filter:brightness(1.05);}
.composer-hint{font-size:var(--text-xs);color:var(--muted);margin:0.3rem 0 0;}
#endOut{white-space:pre-wrap;border:1px solid var(--border);padding:0.5rem 0.55rem;margin-top:0.4rem;background:var(--surface);font-size:var(--text-xs);max-height:160px;overflow:auto;border-radius:var(--field-radius);}
#thinkOut,#techOut{white-space:pre-wrap;border:1px solid var(--border);padding:0.5rem 0.55rem;margin:0.3rem 0 0;background:#fff;font-family:ui-monospace,monospace;font-size:var(--text-xs);max-height:220px;overflow:auto;border-radius:var(--field-radius);}
details.trace{margin-top:0.35rem;}
details.trace>summary{cursor:pointer;font-size:var(--text-sm);color:#475569;padding:0.2rem 0;}
details.end-fold{border:1px solid var(--border);border-radius:var(--radius-panel);background:var(--surface);margin-top:var(--section-gap);padding:0;}
details.end-fold>summary{list-style:none;cursor:pointer;padding:0.45rem 0.65rem;font-size:var(--text-sm);font-weight:600;color:#475569;}
details.end-fold>summary::-webkit-details-marker{display:none;}
.end-inner{padding:0 0.65rem 0.65rem;}
#btnEnd{padding:0.4rem 0.8rem;margin-bottom:0.35rem;}
"""


def _preset_script(
    default_cid: str,
    *,
    mode: Literal["memory", "chat", "debug"] = "memory",
) -> str:
    """浏览器端：多组 client_id/user_id 预设（localStorage）。memory=完整编辑；chat/debug=仅下拉（对话页多会话在外层脚本）。"""
    apply_preset = ""
    if mode == "memory":
        apply_preset = """
  $('cid').value = p.client_id || DEFAULT_CID;
  $('uid').value = p.user_id || '';
  if (typeof clearSessionIfAny === 'function') clearSessionIfAny();
  saveLastIdentity();
  updateIdentityPreview();
  if (!quiet) flashIdentityFeedback('已切换到该预设对应身份。');
"""
    elif mode in ("chat", "debug"):
        apply_preset = """
  if (typeof persistChatSessionState === 'function') persistChatSessionState();
  $('cid').value = p.client_id || DEFAULT_CID;
  $('uid').value = p.user_id || '';
  saveLastIdentity();
  updateIdentityPreview();
  if (typeof onChatIdentityChanged === 'function') onChatIdentityChanged();
  else if (typeof clearSessionIfAny === 'function') clearSessionIfAny();
  if (!quiet) flashIdentityFeedback('已切换身份预设。');
"""
    manual_handler = ""
    if mode == "memory":
        manual_handler = """
  $('btnApplyManual').onclick = () => {
    saveLastIdentity();
    updateIdentityPreview();
    if (typeof clearSessionIfAny === 'function') clearSessionIfAny();
    rebuildPresetSelect();
    const idx = syncPresetSelectToFields();
    const arr = loadPresets();
    if (idx >= 0 && arr[idx]) {
      flashIdentityFeedback('已确认身份；预设下拉已对齐到「' + (arr[idx].label || '预设') + '」。');
    } else {
      flashIdentityFeedback('已确认身份。当前输入与任一预设都不完全一致时，下拉不会自动切选项；可点「保存为预设」新增一条。');
    }
  };
"""
    save_del = ""
    if mode == "memory":
        save_del = """
  $('btnSavePreset').onclick = () => {
    const label = prompt('预设名称（便于识别）');
    if (!label || !label.trim()) return;
    const arr = loadPresets();
    const nm = label.trim();
    arr.push({ client_id: cid(), user_id: uid() || '', label: nm });
    savePresets(arr);
    rebuildPresetSelect();
    requestAnimationFrame(() => {
      const sel = getPresetSelect();
      if (sel) {
        _presetProgrammatic = true;
        sel.value = String(arr.length - 1);
        requestAnimationFrame(() => { _presetProgrammatic = false; });
      }
      flashIdentityFeedback('已保存预设「' + nm + '」，共 ' + arr.length + ' 条；下拉框已选中新项。');
    });
  };
  $('btnDelPreset').onclick = () => {
    const arr = loadPresets();
    if (arr.length <= 1) { alert('至少保留一条预设'); return; }
    const ps = getPresetSelect();
    if (!ps) return;
    const i = parseInt(ps.value, 10);
    if (Number.isNaN(i)) return;
    if (!confirm('删除预设 #' + i + ' ?')) return;
    arr.splice(i, 1);
    savePresets(arr);
    rebuildPresetSelect();
    applyPresetIndex('0', false);
  };
"""
    init_inputs = ""
    if mode == "memory":
        init_inputs = """
  $('cid').addEventListener('input', () => { saveLastIdentity(); updateIdentityPreview(); });
  $('uid').addEventListener('input', () => { saveLastIdentity(); updateIdentityPreview(); });
"""
    else:
        init_inputs = """
  const _cidEl = $('cid');
  const _uidEl = $('uid');
  if (_cidEl && _cidEl.type !== 'hidden') _cidEl.addEventListener('input', () => { saveLastIdentity(); updateIdentityPreview(); });
  if (_uidEl && _uidEl.type !== 'hidden') _uidEl.addEventListener('input', () => { saveLastIdentity(); updateIdentityPreview(); });
"""
    apply_btn = """
  if ($('btnApplyPreset')) $('btnApplyPreset').onclick = () => { const ps = getPresetSelect(); if (ps) applyPresetIndex(ps.value, false); };
"""

    return f"""
const LS_PRESETS = 'ops_web_identity_presets_v1';
const LS_LAST = 'ops_web_last_identity_v1';
const DEFAULT_CID = {json.dumps(default_cid)};

function defaultPresets() {{
  return [{{ client_id: DEFAULT_CID, user_id: '', label: '默认' }}];
}}
/** 过滤 null/非法项，避免 rebuild 时 innerHTML 已清空却 append 失败导致下拉无任何 option */
function loadPresets() {{
  const fallback = defaultPresets();
  try {{
    const raw = localStorage.getItem(LS_PRESETS);
    if (!raw) return fallback;
    const a = JSON.parse(raw);
    if (!Array.isArray(a) || !a.length) return fallback;
    const cleaned = [];
    for (const p of a) {{
      if (!p || typeof p !== 'object') continue;
      const client_id = String(p.client_id != null ? p.client_id : '').trim() || DEFAULT_CID;
      const user_id = String(p.user_id != null ? p.user_id : '').trim();
      const label = String(p.label != null ? p.label : '未命名').trim() || '未命名';
      cleaned.push({{ client_id, user_id, label }});
    }}
    if (!cleaned.length) return fallback;
    if (cleaned.length !== a.length) {{
      try {{ savePresets(cleaned); }} catch (e2) {{}}
    }}
    return cleaned;
  }} catch (e) {{}}
  return fallback;
}}
function savePresets(arr) {{
  localStorage.setItem(LS_PRESETS, JSON.stringify(arr));
}}
function loadLastIdentity() {{
  try {{
    const raw = localStorage.getItem(LS_LAST);
    if (raw) return JSON.parse(raw);
  }} catch (e) {{}}
  return null;
}}
/** 从未保存过时 getItem 为 null；优先用上次身份，避免下拉仍显示服务端 DEFAULT_CID */
function seedPresetsIfMissing() {{
  try {{
    if (localStorage.getItem(LS_PRESETS) !== null) return;
    const last = loadLastIdentity();
    if (last && String(last.client_id || '').trim()) {{
      savePresets([{{
        client_id: String(last.client_id).trim() || DEFAULT_CID,
        user_id: String(last.user_id != null ? last.user_id : '').trim(),
        label: '默认',
      }}]);
    }} else {{
      savePresets(defaultPresets());
    }}
  }} catch (e) {{}}
}}
function saveLastIdentity() {{
  localStorage.setItem(LS_LAST, JSON.stringify({{ client_id: cid(), user_id: uid() || '' }}));
}}
function updateIdentityPreview() {{
  const el = document.getElementById('identityPreview');
  if (!el) return;
  const c = $('cid').value.trim() || DEFAULT_CID;
  const u = $('uid').value.trim();
  el.textContent = u ? c + ' / ' + u : c;
}}
function flashIdentityFeedback(msg) {{
  const el = document.getElementById('identityFeedback');
  if (!el) return;
  el.textContent = msg;
  el.style.color = '#059669';
  if (el._ft) clearTimeout(el._ft);
  el._ft = setTimeout(() => {{ el.textContent = ''; }}, 5500);
}}
let _presetProgrammatic = false;

/** 不依赖全局 `$`，避免与扩展或其它脚本冲突；兼容旧 id presetSel */
function getPresetSelect() {{
  return (
    document.getElementById('identityPresetSel')
    || document.querySelector('select[data-role="identity-preset"]')
    || document.getElementById('presetSel')
    || document.querySelector('.identity-toolbar select.sel-compact')
  );
}}

function renderPresetOutline() {{
  const wrap = document.getElementById('presetOutline');
  const ul = document.getElementById('presetOutlineList');
  if (!wrap || !ul) return;
  const arr = loadPresets();
  ul.innerHTML = '';
  arr.forEach((p, i) => {{
    if (!p) return;
    const li = document.createElement('li');
    li.textContent = (p.label || '未命名') + ' → client_id=' + (p.client_id || DEFAULT_CID) + (p.user_id ? ', user_id=' + p.user_id : '');
    ul.appendChild(li);
  }});
  wrap.hidden = false;
}}

/** 仅一条「默认」预设时与输入框 cid/uid 对齐，避免界面显示 demo_client 而输入框已是其它租户名 */
function syncSingleDefaultPresetFromInputs() {{
  try {{
    const arr = loadPresets();
    if (arr.length !== 1) return;
    const p = arr[0];
    if (!p || String(p.label || '') !== '默认') return;
    const c = ($('cid').value.trim() || DEFAULT_CID);
    const u = $('uid').value.trim();
    const pc = String(p.client_id != null ? p.client_id : '').trim() || DEFAULT_CID;
    const pu = String(p.user_id != null ? p.user_id : '').trim();
    if (pc === c && pu === u) return;
    p.client_id = c;
    p.user_id = u;
    savePresets(arr);
  }} catch (e) {{}}
}}

function appendPresetOptions(sel, arr) {{
  while (sel.options.length) {{ sel.remove(0); }}
  arr.forEach((p, i) => {{
    if (!p || typeof p !== 'object') return;
    const o = document.createElement('option');
    o.value = String(i);
    const c0 = String(p.client_id != null ? p.client_id : '').trim() || DEFAULT_CID;
    const u0 = String(p.user_id != null ? p.user_id : '').trim();
    o.textContent = (p.label || '未命名') + ' — ' + c0 + (u0 ? ' / ' + u0 : '');
    sel.appendChild(o);
  }});
}}

function rebuildPresetSelect() {{
  _presetProgrammatic = true;
  try {{
    syncSingleDefaultPresetFromInputs();
    const sel = getPresetSelect();
    if (!sel) return;
    let arr = loadPresets();
    appendPresetOptions(sel, arr);
    if (sel.options.length === 0) {{
      savePresets(defaultPresets());
      appendPresetOptions(sel, loadPresets());
    }}
    renderPresetOutline();
  }} finally {{
    requestAnimationFrame(() => {{ _presetProgrammatic = false; }});
  }}
}}

/** 根据当前输入框，选中「完全一致」的那条预设；无匹配则不动下拉框。返回匹配下标或 -1 */
function syncPresetSelectToFields() {{
  const arr = loadPresets();
  const c = ($('cid').value.trim() || DEFAULT_CID);
  const u = $('uid').value.trim();
  let idx = -1;
  for (let i = 0; i < arr.length; i++) {{
    const row = arr[i];
    if (!row) continue;
    const pc = (row.client_id || '').trim() || DEFAULT_CID;
    const pu = (row.user_id || '').trim();
    if (pc === c && pu === u) {{ idx = i; break; }}
  }}
  const sel = getPresetSelect();
  if (sel && idx >= 0) {{
    _presetProgrammatic = true;
    sel.value = String(idx);
    requestAnimationFrame(() => {{ _presetProgrammatic = false; }});
  }}
  return idx;
}}

function applyPresetIndex(i, quiet) {{
  const arr = loadPresets();
  const p = arr[parseInt(i, 10)];
  if (!p || typeof p !== 'object') return;
{apply_preset}
}}

function initIdentityUI() {{
  seedPresetsIfMissing();
  const last = loadLastIdentity();
  if (last && last.client_id) {{
    $('cid').value = last.client_id;
    $('uid').value = last.user_id || '';
  }}
  rebuildPresetSelect();
{init_inputs}
  const presetSel = getPresetSelect();
  if (presetSel) {{
    presetSel.addEventListener('change', () => {{
      if (_presetProgrammatic) return;
      applyPresetIndex(presetSel.value, false);
    }});
  }}
{apply_btn}
{save_del}
{manual_handler}
  updateIdentityPreview();
  syncPresetSelectToFields();
}}
"""


def _page_chat_html(default_cid: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>agent-os-runtime · 对话</title>
<style>
{_DEMO_BASE_CSS}
{_IDENTITY_CSS}
{_CHAT_EXTRA_CSS}
</style></head>
<body>
{_nav_html("chat")}
<div class="page-head">
  <h1 class="page-title">对话</h1>
</div>
{_identity_block_chat(default_cid)}

<fieldset class="demo-panel">
<legend>消息</legend>
<div class="row" style="margin:0 0 0.5rem">
  <label class="chk"><input type="checkbox" id="chkSlow" checked/> 启用 Reasoning（慢推理 / Agno <code>reasoning</code>；默认开，与 <code>AGENT_OS_WEB_SLOW</code> 一致，可在此覆盖）</label>
</div>
<div id="thread"></div>
<div class="composer">
  <textarea id="msg" placeholder="输入消息…（Enter 发送，Shift+Enter 换行）" rows="3" autocomplete="off"></textarea>
  <button type="button" class="btn-send" id="go">发送</button>
</div>
<p class="composer-hint">上方为<strong>当前选中的对话</strong>全部轮次（同一服务端 Session 内累计）。身份预设请在 <a href="/memory">记忆管理</a> 维护；调试见 <a href="/debug">流程与 Prompt</a>。</p>
<details class="think trace">
<summary><strong>思考过程</strong> · reasoning</summary>
<p class="hint" style="font-size:0.78rem">若为空：请勾选上方「启用 Reasoning」或设环境变量。工具参数见下一栏。</p>
<pre id="thinkOut">（发送后显示）</pre>
</details>
<details class="tech trace">
<summary><strong>工具与运行细节</strong> · tools / metrics</summary>
<pre id="techOut">（发送后显示）</pre>
</details>
</fieldset>

<details class="end-fold">
<summary>结束本段对话</summary>
<div class="end-inner">
<p class="hint">清空服务端 transcript；可选 AsyncReview 写入 Hindsight（lesson）。</p>
<div class="row"><label>task_id（可选）<input id="taskIdEnd" type="text" placeholder="复盘关联" style="max-width:100%"/></label></div>
<div class="row"><label class="chk"><input type="checkbox" id="chkReview"/> 进行复盘</label></div>
<button type="button" id="btnEnd">结束对话</button>
<div id="endOut"></div>
</div>
</details>

<script>
const $ = (id) => document.getElementById(id);
const jsonHeaders = {{ 'Content-Type': 'application/json' }};

async function post(path, body) {{
  const r = await fetch(path, {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) }});
  const j = await r.json().catch(() => ({{}}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j));
  return j;
}}

{_preset_script(default_cid, mode="chat")}

const cid = () => $('cid').value.trim() || DEFAULT_CID;
const uid = () => {{ const v = $('uid').value.trim(); return v || null; }};
const sid = () => $('sid').value.trim();

const LS_SESS = 'ops_web_chat_sessions_v1';
let currentTurns = [];

function loadSessionStore() {{
  try {{
    const raw = localStorage.getItem(LS_SESS);
    if (raw) return JSON.parse(raw);
  }} catch (e) {{}}
  return {{}};
}}
function saveSessionStore(st) {{
  localStorage.setItem(LS_SESS, JSON.stringify(st));
}}
function genChatLocalId() {{
  return 'c_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
}}
function identityKey() {{
  return ($('cid').value.trim() || DEFAULT_CID) + '::' + ($('uid').value.trim() || '');
}}
function getOrCreateBucket() {{
  const st = loadSessionStore();
  const k = identityKey();
  if (!st[k]) {{
    st[k] = {{ currentId: null, sessions: [] }};
    saveSessionStore(st);
  }}
  return {{ st, k, bucket: st[k] }};
}}

function renderThread(history) {{
  currentTurns = (history && history.length)
    ? history.map((t) => ({{ role: t.role, content: String(t.content) }}))
    : [];
  const el = $('thread');
  el.innerHTML = '';
  if (!currentTurns.length) {{
    const p = document.createElement('p');
    p.className = 'hint';
    p.style.margin = '0';
    p.textContent = '（尚无消息，发送后开始多轮对话）';
    el.appendChild(p);
    return;
  }}
  currentTurns.forEach((turn) => {{
    const wrap = document.createElement('div');
    wrap.className = 'msg ' + (turn.role === 'user' ? 'user' : 'assistant');
    const who = document.createElement('div');
    who.className = 'who';
    who.textContent = turn.role === 'user' ? '你' : '助手';
    const body = document.createElement('div');
    body.className = 'body';
    body.textContent = turn.content;
    wrap.appendChild(who);
    wrap.appendChild(body);
    el.appendChild(wrap);
  }});
  el.scrollTop = el.scrollHeight;
}}

function persistChatSessionState() {{
  try {{
    const {{ st, bucket }} = getOrCreateBucket();
    const cur = bucket.sessions.find((s) => s.id === bucket.currentId);
    if (cur) {{
      cur.turns = currentTurns.slice();
      cur.serverSessionId = $('sid').value.trim() || null;
      cur.updatedAt = Date.now();
      saveSessionStore(st);
    }}
  }} catch (e) {{}}
}}

let _sessionSelProgrammatic = false;

function populateSessionSelect() {{
  const sel = $('sessionSel');
  if (!sel) return;
  const {{ bucket }} = getOrCreateBucket();
  _sessionSelProgrammatic = true;
  try {{
    sel.innerHTML = '';
    bucket.sessions.forEach((s) => {{
      const o = document.createElement('option');
      o.value = s.id;
      const raw = (s.title || '对话') + (s.serverSessionId ? ' · ' + String(s.serverSessionId).slice(0, 8) : '');
      o.textContent = raw.length > 42 ? raw.slice(0, 40) + '…' : raw;
      sel.appendChild(o);
    }});
    if (bucket.currentId) sel.value = bucket.currentId;
  }} finally {{
    requestAnimationFrame(() => {{ _sessionSelProgrammatic = false; }});
  }}
}}

function renderSessionManageList() {{
  const ul = $('sessionManageUl');
  if (!ul) return;
  const {{ bucket }} = getOrCreateBucket();
  ul.innerHTML = '';
  bucket.sessions.forEach((s) => {{
    const li = document.createElement('li');
    if (s.id === bucket.currentId) li.className = 'active';
    const title = document.createElement('span');
    title.className = 'sess-title';
    title.textContent = s.title || '对话';
    const meta = document.createElement('span');
    meta.className = 'sess-meta';
    meta.textContent = s.serverSessionId ? (String(s.serverSessionId).slice(0, 12) + '…') : '未发送';
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'sess-del';
    del.textContent = '删除';
    del.onclick = (e) => {{ e.stopPropagation(); deleteChatSession(s.id); }};
    li.onclick = () => {{
      if (s.id !== bucket.currentId) {{
        switchChatSession(s.id);
        const dlg = $('sessionDialog');
        if (dlg && typeof dlg.close === 'function') dlg.close();
      }}
    }};
    li.appendChild(title);
    li.appendChild(meta);
    li.appendChild(del);
    ul.appendChild(li);
  }});
}}

function renderSessionUI() {{
  populateSessionSelect();
  renderSessionManageList();
}}

function switchChatSession(localId) {{
  persistChatSessionState();
  const {{ st, bucket }} = getOrCreateBucket();
  if (!bucket.sessions.find((s) => s.id === localId)) return;
  bucket.currentId = localId;
  saveSessionStore(st);
  const cur = bucket.sessions.find((s) => s.id === localId);
  $('sid').value = cur.serverSessionId || '';
  renderThread(cur.turns || []);
  $('thinkOut').textContent = '';
  $('techOut').textContent = '';
  renderSessionUI();
}}

function deleteChatSession(localId) {{
  const {{ st, bucket }} = getOrCreateBucket();
  if (bucket.sessions.length <= 1) {{ alert('至少保留一条对话'); return; }}
  if (!confirm('删除该对话的本地记录？（不影响服务端已结束的 Session）')) return;
  bucket.sessions = bucket.sessions.filter((s) => s.id !== localId);
  if (bucket.currentId === localId)
    bucket.currentId = bucket.sessions[0] ? bucket.sessions[0].id : null;
  if (!bucket.currentId && bucket.sessions.length) bucket.currentId = bucket.sessions[0].id;
  saveSessionStore(st);
  loadSessionsForIdentity();
}}

function newChatSession() {{
  persistChatSessionState();
  const {{ st, bucket }} = getOrCreateBucket();
  const id = genChatLocalId();
  bucket.sessions.unshift({{ id, serverSessionId: null, title: '新对话', turns: [], updatedAt: Date.now() }});
  bucket.currentId = id;
  saveSessionStore(st);
  $('sid').value = '';
  renderThread([]);
  $('thinkOut').textContent = '';
  $('techOut').textContent = '';
  renderSessionUI();
}}

function loadSessionsForIdentity() {{
  const {{ st, bucket }} = getOrCreateBucket();
  if (!bucket.sessions.length) {{
    const id = genChatLocalId();
    bucket.sessions.push({{ id, serverSessionId: null, title: '新对话', turns: [], updatedAt: Date.now() }});
    bucket.currentId = id;
    saveSessionStore(st);
  }}
  if (!bucket.currentId || !bucket.sessions.find((s) => s.id === bucket.currentId))
    bucket.currentId = bucket.sessions[0].id;
  saveSessionStore(st);
  const cur = bucket.sessions.find((s) => s.id === bucket.currentId);
  $('sid').value = cur.serverSessionId || '';
  renderThread(cur.turns || []);
  renderSessionUI();
}}

function onChatIdentityChanged() {{
  loadSessionsForIdentity();
}}

function renderTrace(t) {{
  const thinkEl = $('thinkOut');
  const techEl = $('techOut');
  if (!t || typeof t !== 'object') {{
    thinkEl.textContent = '（无 trace）';
    techEl.textContent = '（无）';
    return;
  }}
  const parts = [];
  if (t.reasoning_content) parts.push(String(t.reasoning_content));
  if (t.reasoning_steps && t.reasoning_steps.length)
    parts.push(JSON.stringify(t.reasoning_steps, null, 2));
  thinkEl.textContent = parts.length
    ? parts.join('\\n\\n--- reasoning_steps ---\\n\\n')
    : '（本步无 reasoning_content / reasoning_steps。开启 AGENT_OS_WEB_SLOW=1 且模型支持「思考」后，此处可出现类似 DeepSeek / Gemini 的思考文本；工具参数见下一栏。）';
  const tech = {{ ...t }};
  delete tech.reasoning_content;
  delete tech.reasoning_steps;
  const keys = Object.keys(tech).filter((k) => tech[k] != null && tech[k] !== '');
  techEl.textContent = keys.length ? JSON.stringify(tech, null, 2) : '（无 tools/metrics/events 等）';
}}

function clearSessionIfAny() {{
  $('sid').value = '';
  currentTurns = [];
  renderThread([]);
  const {{ st, bucket }} = getOrCreateBucket();
  const cur = bucket.sessions.find((s) => s.id === bucket.currentId);
  if (cur) {{
    cur.turns = [];
    cur.serverSessionId = null;
    saveSessionStore(st);
  }}
  $('thinkOut').textContent = '';
  $('techOut').textContent = '';
}}

initIdentityUI();
loadSessionsForIdentity();

(function wireSessionButtons() {{
  const btnCopy = $('btnCopySid');
  const btnNew = $('btnNewChat');
  const btnDlg = $('btnManageSessions');
  const dlg = $('sessionDialog');
  const btnClose = $('btnCloseSessionDialog');
  const btnNewDlg = $('btnNewChatDialog');
  const sessionSel = $('sessionSel');
  if (sessionSel) {{
    sessionSel.addEventListener('change', () => {{
      if (_sessionSelProgrammatic) return;
      switchChatSession(sessionSel.value);
    }});
  }}
  if (btnCopy) {{
    btnCopy.onclick = async () => {{
      const s = ($('sid') && $('sid').value) ? $('sid').value.trim() : '';
      if (!s) {{ flashIdentityFeedback('当前没有可复制的 Session'); return; }}
      try {{
        await navigator.clipboard.writeText(s);
        flashIdentityFeedback('已复制 Session 到剪贴板');
      }} catch (e) {{ alert(String(e)); }}
    }};
  }}
  if (btnNew) btnNew.onclick = () => newChatSession();
  if (btnDlg && dlg) {{
    btnDlg.onclick = () => {{
      renderSessionManageList();
      if (typeof dlg.showModal === 'function') dlg.showModal();
    }};
  }}
  if (btnClose && dlg) btnClose.onclick = () => dlg.close();
  if (dlg) {{
    dlg.addEventListener('click', (e) => {{ if (e.target === dlg) dlg.close(); }});
  }}
  if (btnNewDlg) {{
    btnNewDlg.onclick = () => {{
      newChatSession();
      if (dlg && typeof dlg.close === 'function') dlg.close();
    }};
  }}
}})();

const LS_SLOW = 'ops_web_use_slow';
function useSlow() {{ return $('chkSlow').checked; }}
function initSlowToggle() {{
  try {{
    if (localStorage.getItem(LS_SLOW) === '0') $('chkSlow').checked = false;
    else $('chkSlow').checked = true;
  }} catch (e) {{ $('chkSlow').checked = true; }}
  $('chkSlow').addEventListener('change', () => {{
    localStorage.setItem(LS_SLOW, $('chkSlow').checked ? '1' : '0');
  }});
}}
initSlowToggle();

const btnGo = $('go');
$('msg').addEventListener('keydown', (e) => {{
  if (e.key !== 'Enter' || e.shiftKey) return;
  e.preventDefault();
  if (!btnGo.disabled) btnGo.click();
}});

$('go').onclick = async () => {{
  const message = $('msg').value.trim();
  if (!message) return;
  const prevLabel = btnGo.textContent;
  btnGo.disabled = true;
  btnGo.textContent = '发送中';
  $('thinkOut').textContent = '…';
  $('techOut').textContent = '…';
  try {{
    const j = await post('/chat', {{
      message,
      session_id: sid() || null,
      client_id: cid(),
      user_id: uid(),
      use_slow_reasoning: useSlow(),
      include_trace: true,
    }});
    $('sid').value = j.session_id;
    let hist;
    if (j.history && j.history.length) hist = j.history;
    else hist = [{{ role: 'user', content: message }}, {{ role: 'assistant', content: j.reply }}];
    renderThread(hist);
    renderTrace(j.trace);
    try {{
      const {{ st, bucket }} = getOrCreateBucket();
      const cur = bucket.sessions.find((s) => s.id === bucket.currentId);
      if (cur) {{
        cur.serverSessionId = j.session_id;
        cur.turns = currentTurns.slice();
        cur.updatedAt = Date.now();
        const fu = cur.turns.find((t) => t.role === 'user');
        if (fu && fu.content) cur.title = fu.content.slice(0, 28) + (fu.content.length > 28 ? '…' : '');
        saveSessionStore(st);
      }}
    }} catch (e) {{}}
    renderSessionUI();
    $('msg').value = '';
    saveLastIdentity();
    $('msg').focus();
  }} catch (e) {{
    const el = $('thread');
    el.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'hint';
    p.style.color = '#b91c1c';
    p.textContent = String(e);
    el.appendChild(p);
    $('thinkOut').textContent = '';
    $('techOut').textContent = '';
  }} finally {{
    btnGo.disabled = false;
    btnGo.textContent = prevLabel;
  }}
}};

$('btnEnd').onclick = async () => {{
  const s = sid();
  if (!s) {{ alert('尚无 session_id'); return; }}
  $('endOut').textContent = '…';
  try {{
    const j = await post('/api/session/end', {{
      session_id: s,
      client_id: cid(),
      user_id: uid(),
      use_slow_reasoning: useSlow(),
      task_id: $('taskIdEnd').value.trim() || null,
      run_review: $('chkReview').checked,
    }});
    $('endOut').textContent = JSON.stringify(j, null, 2);
    $('sid').value = '';
    $('chkReview').checked = false;
    renderThread([]);
    try {{
      const {{ st, bucket }} = getOrCreateBucket();
      const cur = bucket.sessions.find((s) => s.id === bucket.currentId);
      if (cur) {{
        cur.turns = [];
        cur.serverSessionId = null;
        saveSessionStore(st);
      }}
    }} catch (e) {{}}
    renderSessionUI();
    $('thinkOut').textContent = '';
    $('techOut').textContent = '';
  }} catch (e) {{ $('endOut').textContent = String(e); }}
}};
</script>
</body></html>
"""


def _page_memory_html(default_cid: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>agent-os-runtime · 记忆管理</title>
<style>
{_DEMO_BASE_CSS}
{_IDENTITY_CSS}
</style></head>
<body>
{_nav_html("memory")}
<h1 class="page-title">记忆管理</h1>
<p class="hint">列表与写入均针对下方 <strong>当前身份</strong>。与对话页共用「启用 Reasoning」开关（浏览器 <code>localStorage</code> <code>ops_web_use_slow</code>）。结束对话与复盘请在 <a href="/">对话页</a>；指令栈见 <a href="/debug">流程与 Prompt</a>。</p>
{_identity_block_memory(default_cid, page_note="在此编辑 <strong>client_id / user_id</strong> 并维护<strong>身份预设</strong>；对话页下拉选项来自此处。", open_default=True)}

<fieldset class="demo-panel demo-panel--emerald">
<legend>画像层（Mem0 或本地 local_memory.json）</legend>
<div class="mem-toolbar">
  <button type="button" class="btn-primary" id="btnProfRefresh">刷新画像列表</button>
  <span id="profHint" style="font-size:0.82rem;color:#64748b"></span>
</div>
<div id="memList">点击「刷新画像列表」</div>
</fieldset>

<fieldset class="demo-panel demo-panel--violet">
<legend>Hindsight（hindsight.jsonl）</legend>
<div class="mem-toolbar">
  <button type="button" class="btn-primary" id="btnHindRefresh">刷新 Hindsight</button>
</div>
<div id="hindList">点击「刷新 Hindsight」</div>
</fieldset>

<fieldset class="demo-panel demo-panel--amber">
<legend>手动写入（等价 record_*）</legend>
<p class="hint">Web 演示下模型侧 <code>record_*</code> 已关闭；此处与 <code>POST /api/memory/ingest</code> 一致。</p>
<p class="in-block">事实</p>
<textarea id="txFact" placeholder="长期事实…"></textarea>
<p class="row"><button type="button" class="btn-primary" id="btnFact">写入事实</button></p>
<p class="in-block">偏好</p>
<textarea id="txPref" placeholder="偏好…"></textarea>
<p class="row"><button type="button" class="btn-primary" id="btnPref">写入偏好</button></p>
<p class="in-block">任务反馈</p>
<textarea id="txFb" placeholder="任务反馈…"></textarea>
<div class="row"><label>task_id（可选）<input id="taskId" type="text" placeholder="可选"/></label></div>
<p class="row"><button type="button" class="btn-primary" id="btnFb">写入任务反馈</button></p>
<div id="memOut"></div>
</fieldset>

<script>
const $ = (id) => document.getElementById(id);
const jsonHeaders = {{ 'Content-Type': 'application/json' }};

async function post(path, body) {{
  const r = await fetch(path, {{ method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) }});
  const j = await r.json().catch(() => ({{}}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j));
  return j;
}}
async function getq(path) {{
  const r = await fetch(path);
  const j = await r.json().catch(() => ({{}}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j));
  return j;
}}

{_preset_script(default_cid)}

const cid = () => $('cid').value.trim() || DEFAULT_CID;
const uid = () => {{ const v = $('uid').value.trim(); return v || null; }};
const LS_SLOW = 'ops_web_use_slow';
function useSlowFromStorage() {{
  try {{ return localStorage.getItem(LS_SLOW) !== '0'; }} catch (e) {{ return true; }}
}}

function qUser() {{
  const u = uid();
  return u ? ('&user_id=' + encodeURIComponent(u)) : '';
}}

function esc(s) {{
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}

async function refreshProfile() {{
  $('memList').textContent = '…';
  $('profHint').textContent = '';
  try {{
    const j = await getq('/api/memory/profile/list?client_id=' + encodeURIComponent(cid()) + qUser() + '&use_slow=' + useSlowFromStorage());
    $('profHint').textContent = (j.backend || '') + ' | ' + (j.path || '');
    if (j.delete_note) $('profHint').textContent += ' — ' + j.delete_note;
    if (!j.items || !j.items.length) {{ $('memList').textContent = '（无条目）'; return; }}
    if (j.backend === 'mem0') {{
      $('memList').innerHTML = '<table class="memtbl"><tr><th>#</th><th>text</th></tr>' +
        j.items.map((it, i) => '<tr><td>' + (i+1) + '</td><td>' + esc(it.text) + '</td></tr>').join('') + '</table>';
      return;
    }}
    $('memList').innerHTML = '<table class="memtbl"><tr><th>index</th><th>text</th><th></th></tr>' +
      j.items.map(it => '<tr><td>' + it.index + '</td><td>' + esc(it.text) + '</td><td><button type="button" class="delProf" data-idx="' + it.index + '">删除</button></td></tr>').join('') + '</table>';
    document.querySelectorAll('.delProf').forEach(btn => {{
      btn.onclick = async () => {{
        if (!confirm('删除 index=' + btn.dataset.idx + ' ?')) return;
        try {{
          await post('/api/memory/profile/delete-local', {{ client_id: cid(), user_id: uid(), use_slow_reasoning: useSlowFromStorage(), index: parseInt(btn.dataset.idx,10) }});
          await refreshProfile();
        }} catch (e) {{ alert(e); }}
      }};
    }});
  }} catch (e) {{ $('memList').textContent = String(e); }}
}}

async function refreshHind() {{
  $('hindList').textContent = '…';
  try {{
    const j = await getq('/api/memory/hindsight/list?client_id=' + encodeURIComponent(cid()) + '&use_slow=' + useSlowFromStorage());
    if (!j.items || !j.items.length) {{ $('hindList').textContent = '（无条目）'; return; }}
    $('hindList').innerHTML = '<table class="memtbl"><tr><th>行号</th><th>内容</th><th></th></tr>' +
      j.items.map(it => '<tr><td>' + it.file_line + '</td><td>' + esc(JSON.stringify(it.row)) + '</td><td><button type="button" class="delHind" data-line="' + it.file_line + '">删除</button></td></tr>').join('') + '</table>';
    document.querySelectorAll('.delHind').forEach(btn => {{
      btn.onclick = async () => {{
        if (!confirm('删除 hindsight 第 ' + btn.dataset.line + ' 行？')) return;
        try {{
          await post('/api/memory/hindsight/delete-line', {{ client_id: cid(), use_slow_reasoning: useSlowFromStorage(), file_line: parseInt(btn.dataset.line,10) }});
          await refreshHind();
        }} catch (e) {{ alert(e); }}
      }};
    }});
  }} catch (e) {{ $('hindList').textContent = String(e); }}
}}

async function ingest(kind, textEl, extra = {{}}) {{
  const text = $(textEl).value.trim();
  if (!text) {{ alert('请先填写内容'); return; }}
  $('memOut').textContent = '…';
  try {{
    const j = await post('/api/memory/ingest', {{
      client_id: cid(),
      user_id: uid(),
      use_slow_reasoning: useSlowFromStorage(),
      kind,
      text,
      ...extra,
    }});
    $('memOut').textContent = JSON.stringify(j, null, 2);
    if (kind !== 'feedback') $(textEl).value = '';
    await refreshProfile();
    if (kind === 'feedback') await refreshHind();
  }} catch (e) {{ $('memOut').textContent = String(e); }}
}}

initIdentityUI();
$('btnProfRefresh').onclick = () => {{ saveLastIdentity(); refreshProfile(); }};
$('btnHindRefresh').onclick = () => {{ saveLastIdentity(); refreshHind(); }};
$('btnFact').onclick = () => ingest('fact', 'txFact');
$('btnPref').onclick = () => ingest('preference', 'txPref');
$('btnFb').onclick = () => ingest('feedback', 'txFb', {{ task_id: $('taskId').value.trim() || null }});
</script>
</body></html>
"""


def _page_debug_html(default_cid: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>agent-os-runtime · 流程与 Prompt</title>
<style>
{_DEMO_BASE_CSS}
{_IDENTITY_CSS}
</style></head>
<body>
{_nav_html("debug")}
<h1 class="page-title">流程与 Prompt</h1>
<p class="hint">展示<strong>当前身份预设</strong>下与 <a href="/">对话页</a>一致的 Agent；增删预设请到 <a href="/memory">记忆管理</a>。对话页「启用 Reasoning」存在浏览器 <code>localStorage</code>，本页刷新时会带上同一开关以匹配缓存。改 <code>.env</code> 后请重启服务。</p>
{_identity_block_chat(default_cid, show_session_panel=False)}

<fieldset class="demo-panel">
<legend>运行时摘要</legend>
<div class="mem-toolbar">
  <button type="button" class="btn-primary" id="btnInspectRefresh">刷新</button>
</div>
<div id="inspectMeta"></div>
</fieldset>

<fieldset class="demo-panel demo-panel--slate">
<legend>Agent 执行工作流（调试主视图）</legend>
<p class="hint"><strong>指令栈（instructions）</strong>：在 Agno 里会作为<strong>系统侧指令</strong>注入模型（多条列表会合并进同一次 system 上下文）。日常说「系统 prompt」时，通常包含这些条目里与人设、业务规则相关的文字，但<strong>不</strong>等同于某一条用户消息。</p>
<p class="hint"><strong>Reasoning（慢推理）</strong>：由对话页勾选或环境变量 <code>AGENT_OS_WEB_SLOW</code>（默认视为开启）控制；开启时工厂设置 <code>thought_mode=slow</code>，Agno 可走多步 <code>reasoning</code>；关闭则 fast，通常无 <code>reasoning_content</code>。</p>
<div class="flow-wrap">
  <div class="flow-title">一轮对话的主路径（你要调试的核心）</div>
  <div class="flow-row">
    <div class="flow-node flow-node--hi"><strong>① 指令栈</strong>system 指令（见下方列表）</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node flow-node--hi"><strong>② 可选 Reasoning</strong>Agno 多步推理</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node"><strong>③ 模型 + 工具</strong>含 retrieve / 业务工具</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node flow-node--ok"><strong>④ 输出</strong>回复；对话页可看 trace</div>
  </div>
</div>
<div class="flow-wrap">
  <div class="flow-title">工具内检索链（retrieve_ordered_context）</div>
  <div class="flow-row">
    <div class="flow-node flow-node--hi"><strong>①</strong> Mem0 / 本地画像</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node flow-node--hi"><strong>②</strong> Hindsight</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node"><strong>③</strong> 领域知识（未挂载则提示）</div>
  </div>
</div>
</fieldset>

<fieldset class="demo-panel demo-panel--emerald">
<legend>系统指令栈（instructions）</legend>
<p class="hint">下列顺序与 <code>get_agent</code> 注入一致（skill manifest、handoff、golden、Web 附加说明等）。调试时改提示文本请优先改 <code>AGENT_OS_MANIFEST_DIR</code> 下对应 <code>{{skill_id}}.json</code> 或内置配方。</p>
<ol id="instrList" class="instr-ol"></ol>
</fieldset>

<fieldset class="demo-panel demo-panel--violet">
<legend>工具（当前 Agent 绑定）</legend>
<p class="hint">删除线表示 Web 演示已从 Agent 移除，仍可通过记忆页 / API 写入。</p>
<div id="toolTags" class="tool-tags"></div>
</fieldset>

<fieldset class="demo-panel demo-panel--amber">
<legend>Manifest（skill 注册表 / AGENT_OS_MANIFEST_DIR）</legend>
<pre id="manifestPre" class="code-block">加载中…</pre>
</fieldset>

<fieldset class="demo-panel">
<legend>相关路径</legend>
<pre id="pathsPre" class="code-block">加载中…</pre>
</fieldset>

<script>
const $ = (id) => document.getElementById(id);
async function getq(path) {{
  const r = await fetch(path);
  const j = await r.json().catch(() => ({{}}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j));
  return j;
}}

{_preset_script(default_cid, mode="debug")}

const cid = () => $('cid').value.trim() || DEFAULT_CID;
const uid = () => {{ const v = $('uid').value.trim(); return v || null; }};
const LS_SLOW = 'ops_web_use_slow';
function useSlowFromStorage() {{
  try {{ return localStorage.getItem(LS_SLOW) !== '0'; }} catch (e) {{ return true; }}
}}

function esc(s) {{
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}

function renderInspect(j) {{
  const meta = $('inspectMeta');
  meta.innerHTML = '';
  const dl = document.createElement('dl');
  dl.className = 'meta-dl';
  const rows = [
    ['Agent', j.agent_name || '—'],
    ['Model', j.model_id || '—'],
    ['Reasoning（Agno）', j.reasoning === true ? ('是 ' + (j.reasoning_min_steps != null ? '(' + j.reasoning_min_steps + '–' + j.reasoning_max_steps + ' 步)' : '')) : String(j.reasoning)],
    ['本页开关（已应用）', String(j.use_slow_reasoning_applied)],
    ['环境默认（未传参时）', String(j.env_slow_default)],
    ['Markdown', String(j.markdown)],
    ['Skill（解析后）', j.skill_id_resolved || '—'],
    ['AGENT_OS_WEB_SKILL_ID', j.env_flags.AGENT_OS_WEB_SKILL_ID || '—'],
    ['AGENT_OS_WEB_NO_KNOWLEDGE', String(j.env_flags.AGENT_OS_WEB_NO_KNOWLEDGE)],
  ];
  rows.forEach(([k, v]) => {{
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    dl.appendChild(dt);
    dl.appendChild(dd);
  }});
  meta.appendChild(dl);

  $('instrList').innerHTML = (j.instructions || []).map((t) => '<li>' + esc(t) + '</li>').join('') || '<li>（无）</li>';

  const ex = new Set(j.web_excluded_tools || []);
  $('toolTags').innerHTML = (j.tools || []).map((name) => {{
    const off = ex.has(name);
    return '<span class="tool-tag' + (off ? ' tool-tag--off' : '') + '" title="' + (off ? 'Web 已从 Agent 移除此工具' : '') + '">' + esc(name) + '</span>';
  }}).join('') || '<span class="hint">（无工具）</span>';

  $('manifestPre').textContent = j.manifest ? JSON.stringify(j.manifest, null, 2) : '（未配置 manifest 或文件无效）';
  $('pathsPre').textContent = JSON.stringify(j.paths, null, 2);
}}

async function loadInspect() {{
  $('inspectMeta').innerHTML = '<p class="hint">加载中…</p>';
  $('instrList').innerHTML = '';
  $('toolTags').innerHTML = '';
  $('manifestPre').textContent = '…';
  $('pathsPre').textContent = '…';
  try {{
    const params = new URLSearchParams({{ client_id: cid() }});
    const u = $('uid').value.trim();
    if (u) params.set('user_id', u);
    params.set('use_slow', useSlowFromStorage() ? 'true' : 'false');
    const j = await getq('/api/agent/inspect?' + params.toString());
    renderInspect(j);
  }} catch (e) {{
    $('inspectMeta').innerHTML = '<p class="hint" style="color:#b91c1c">' + esc(String(e)) + '</p>';
  }}
}}

function onChatIdentityChanged() {{ loadInspect(); }}

initIdentityUI();
$('btnInspectRefresh').onclick = () => {{ saveLastIdentity(); loadInspect(); }};
loadInspect();
</script>
</body></html>
"""


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def page_chat():
    default_cid = os.getenv("AGENT_OS_WEB_CLIENT_ID", "demo_client")
    return HTMLResponse(_page_chat_html(default_cid))


@app.get("/memory", response_class=HTMLResponse)
def page_memory():
    default_cid = os.getenv("AGENT_OS_WEB_CLIENT_ID", "demo_client")
    return HTMLResponse(_page_memory_html(default_cid))


@app.get("/debug", response_class=HTMLResponse)
def page_debug():
    default_cid = os.getenv("AGENT_OS_WEB_CLIENT_ID", "demo_client")
    return HTMLResponse(_page_debug_html(default_cid))


@app.get("/api/agent/inspect")
def api_agent_inspect(
    client_id: str = "demo_client",
    user_id: str | None = None,
    skill_id: str | None = Query(
        None, description="与对话请求 skill_id 对齐；省略则 AGENT_OS_WEB_SKILL_ID / 默认"
    ),
    use_slow: bool | None = Query(
        None,
        description="与对话页「启用 Reasoning」一致；省略时采用环境默认（AGENT_OS_WEB_SLOW，默认开启）",
    ),
):
    """返回当前 Web 进程内与对话一致的 Agent 元数据、指令栈与工具名（供 /debug 调试）。"""
    cid = (client_id or "").strip() or "demo_client"
    uid = (user_id or "").strip() or None
    return _agent_inspect_payload(cid, uid, use_slow, skill_id=skill_id)


@app.get("/api/admin/graphiti-entitlements")
def api_graphiti_entitlements_get(request: Request):
    _assert_admin_request_allowed(request)
    p = _graphiti_entitlements_path()
    return {"path": str(p), "data": load_entitlements_file(p)}


@app.post("/api/admin/graphiti-entitlements/global")
def api_graphiti_entitlements_set_global(inp: GraphitiEntitlementsGlobalIn, request: Request):
    _assert_admin_request_allowed(request)
    payload = inp.model_dump()
    cached = _idempotency_cache_check(request, payload=payload)
    if cached is not None:
        return cached
    p = _graphiti_entitlements_path()
    try:
        before, doc = update_entitlements_file(
            p,
            expected_revision=inp.expected_revision,
            mutator=lambda cur: cur.__setitem__(
                "global_allowed_skill_ids",
                sorted({str(x).strip() for x in inp.skills if str(x).strip()}),
            ),
        )
    except EntitlementsRevisionConflictError as e:
        raise HTTPException(
            409,
            detail={
                "code": "revision_conflict",
                "message": str(e),
                "expected_revision": e.expected,
                "actual_revision": e.actual,
                "hint": f"请刷新后使用 expected_revision={e.actual} 重试",
            },
        ) from e
    append_entitlements_audit(
        action="set_global",
        actor=_admin_actor(request),
        source="web.admin.graphiti-entitlements.global",
        entitlements_path=p,
        before=before,
        after=doc,
        metadata={
            "request_id": getattr(request.state, "request_id", "-"),
            "client_host": getattr(request.client, "host", None),
            "expected_revision": inp.expected_revision,
        },
    )
    resp = {"status": "ok", "path": str(p), "data": doc}
    _idempotency_cache_store(request, payload=payload, response_payload=resp)
    return resp


@app.post("/api/admin/graphiti-entitlements/client")
def api_graphiti_entitlements_set_client(inp: GraphitiEntitlementsUpsertIn, request: Request):
    _assert_admin_request_allowed(request)
    payload = inp.model_dump()
    cached = _idempotency_cache_check(request, payload=payload)
    if cached is not None:
        return cached
    p = _graphiti_entitlements_path()
    try:
        before, doc = update_entitlements_file(
            p,
            expected_revision=inp.expected_revision,
            mutator=lambda cur: cur.setdefault("client_entitlements", {}).__setitem__(
                inp.client_id, sorted({str(x).strip() for x in inp.skills if str(x).strip()})
            ),
        )
    except EntitlementsRevisionConflictError as e:
        raise HTTPException(
            409,
            detail={
                "code": "revision_conflict",
                "message": str(e),
                "expected_revision": e.expected,
                "actual_revision": e.actual,
                "hint": f"请刷新后使用 expected_revision={e.actual} 重试",
            },
        ) from e
    append_entitlements_audit(
        action="set_client",
        actor=_admin_actor(request),
        source="web.admin.graphiti-entitlements.client",
        entitlements_path=p,
        before=before,
        after=doc,
        metadata={
            "request_id": getattr(request.state, "request_id", "-"),
            "client_host": getattr(request.client, "host", None),
            "client_id": inp.client_id,
            "expected_revision": inp.expected_revision,
        },
    )
    resp = {"status": "ok", "path": str(p), "data": doc}
    _idempotency_cache_store(request, payload=payload, response_payload=resp)
    return resp


@app.delete("/api/admin/graphiti-entitlements/client/{client_id}")
def api_graphiti_entitlements_delete_client(
    client_id: str,
    request: Request,
    expected_revision: int | None = Query(None, ge=0),
):
    _assert_admin_request_allowed(request)
    cid = (client_id or "").strip()
    if not cid:
        raise HTTPException(400, detail="client_id 不能为空")
    payload = {"client_id": cid, "expected_revision": expected_revision}
    cached = _idempotency_cache_check(request, payload=payload)
    if cached is not None:
        return cached
    p = _graphiti_entitlements_path()
    try:
        before, doc = update_entitlements_file(
            p,
            expected_revision=expected_revision,
            mutator=lambda cur: cur.setdefault("client_entitlements", {}).pop(cid, None),
        )
    except EntitlementsRevisionConflictError as e:
        raise HTTPException(
            409,
            detail={
                "code": "revision_conflict",
                "message": str(e),
                "expected_revision": e.expected,
                "actual_revision": e.actual,
                "hint": f"请刷新后使用 expected_revision={e.actual} 重试",
            },
        ) from e
    append_entitlements_audit(
        action="remove_client",
        actor=_admin_actor(request),
        source="web.admin.graphiti-entitlements.client",
        entitlements_path=p,
        before=before,
        after=doc,
        metadata={
            "request_id": getattr(request.state, "request_id", "-"),
            "client_host": getattr(request.client, "host", None),
            "client_id": cid,
            "expected_revision": expected_revision,
        },
    )
    resp = {"status": "ok", "path": str(p), "data": doc}
    _idempotency_cache_store(request, payload=payload, response_payload=resp)
    return resp


@app.get("/api/session/messages")
def api_session_messages(
    session_id: str = Query(
        ...,
        min_length=1,
        description="与 POST /chat 使用同一值；F5 后仍由 localStorage 提供",
    ),
    client_id: str = "demo_client",
    user_id: str | None = None,
    skill_id: str | None = Query(None, description="与当时对话 bundle 的 skill 一致"),
    use_slow: bool | None = Query(
        None,
        description="与对话页「启用 Reasoning」一致；省略时采用环境默认",
    ),
    limit: int = Query(200, ge=1, le=2000, description="最多返回条数（消息数）"),
):
    """
    从 Agno 会话存储读取该 ``session_id`` 下的消息。用于**进程重启**后，前端在仅有 ``session_id`` 时
    补全展示（与 ``AGENT_OS_SESSION_HISTORY_MAX_MESSAGES`` 所注入到模型的条数可分开配置）。
    """
    settings = Settings.from_env()
    if not settings.enable_session_db:
        raise HTTPException(400, detail="未启用会话落库 (AGENT_OS_ENABLE_SESSION_DB=0)")
    cid = (client_id or "").strip() or "demo_client"
    uid = (user_id or "").strip() or None
    slow_applied = _resolve_use_slow(use_slow)
    _s, _c, agent = _get_bundle_for(cid, uid, slow_applied, skill_id=skill_id)
    if getattr(agent, "db", None) is None:
        raise HTTPException(503, detail="当前 Agent 未挂载会话库")
    raw = agent.get_session_messages(
        session_id=session_id.strip(),
        limit=limit,
        skip_history_messages=False,
    )
    items: list[dict[str, str]] = []
    for m in raw:
        role = getattr(m, "role", None) or "assistant"
        c = getattr(m, "content", None)
        content = c if isinstance(c, str) else str(c)
        items.append({"role": str(role), "content": content})
    return {
        "session_id": session_id.strip(),
        "use_slow_reasoning_applied": slow_applied,
        "messages": items,
        "count": len(items),
    }


@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, request: Request):
    if not inp.message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    slow_applied = _resolve_use_slow(inp.use_slow_reasoning)
    settings, ctrl, agent = _get_bundle_for(
        inp.client_id, inp.user_id, slow_applied, skill_id=inp.skill_id
    )
    sid = inp.session_id or new_session_id()
    rid = getattr(request.state, "request_id", "-")
    try:
        ctrl.bump_turn_and_maybe_snapshot(inp.client_id, inp.user_id)
        effective_skill_id = _effective_skill_for_context(settings, inp.skill_id)
        task_store, task_summary_service = _task_memory_from_settings(settings)
        active_task_id: str | None = None
        if task_store is not None:
            task = task_store.get_or_create_active_task(
                session_id=sid,
                client_id=inp.client_id,
                user_id=inp.user_id,
                skill_id=effective_skill_id,
                seed_message=inp.message,
            )
            active_task_id = task.task_id
            task_store.append_message(
                session_id=sid,
                task_id=active_task_id,
                role="user",
                content=inp.message,
            )
        run_message = inp.message
        context_diagnostics: dict[str, Any] | None = None
        builder = _context_builder_from_settings(settings)
        if builder is not None:
            manifest_registry = load_skill_manifest_registry(settings.agent_manifest_dir)
            effective_manifest = manifest_registry.get(effective_skill_id)
            current_summary = (
                task_store.get_summary(session_id=sid, task_id=active_task_id)
                if task_store is not None and active_task_id is not None
                else None
            )
            task_index = (
                task_store.task_index(session_id=sid)
                if task_store is not None and active_task_id is not None
                else None
            )
            hist_cap = effective_session_history_max_messages(
                base_max_messages=settings.session_history_max_messages,
                task_summary=current_summary,
                cap_when_summary_present=settings.session_history_cap_when_task_summary,
            )
            retrieved_context = None
            retrieve_mode = (
                effective_manifest.auto_retrieve_mode
                if effective_manifest and effective_manifest.auto_retrieve_mode
                else settings.context_auto_retrieve_mode
            )
            retrieve_keywords = (
                tuple(effective_manifest.auto_retrieve_keywords)
                if effective_manifest and effective_manifest.auto_retrieve_keywords
                else settings.context_auto_retrieve_keywords
            )
            retrieve_decision = resolve_auto_retrieve_decision(
                inp.message, mode=retrieve_mode, keywords=retrieve_keywords
            )
            if settings.enable_context_auto_retrieve and retrieve_decision.enabled:
                asset_store = getattr(agent, "_agent_os_asset_store", None)
                if asset_store is None:
                    asset_store = asset_store_from_settings(
                        enable=settings.enable_asset_store, path=settings.asset_store_path
                    )
                knowledge = getattr(agent, "_agent_os_knowledge", None)
                if knowledge is None and not _no_knowledge:
                    knowledge = GraphitiReadService.from_env(settings.knowledge_fallback_path)
                retrieved_context = build_auto_retrieval_context(
                    ctrl,
                    inp.message,
                    client_id=inp.client_id,
                    user_id=inp.user_id,
                    skill_id=effective_skill_id,
                    enable_hindsight=settings.enable_hindsight,
                    enable_temporal_grounding=settings.enable_temporal_grounding,
                    knowledge=knowledge,
                    enable_asset_store=settings.enable_asset_store,
                    asset_store=asset_store,
                    enable_hindsight_synthesis=settings.enable_hindsight_synthesis,
                    hindsight_synthesis_model=settings.hindsight_synthesis_model,
                    hindsight_synthesis_max_candidates=settings.hindsight_synthesis_max_candidates,
                    enable_asset_synthesis=settings.enable_asset_synthesis,
                    asset_synthesis_model=settings.asset_synthesis_model,
                    asset_synthesis_max_candidates=settings.asset_synthesis_max_candidates,
                )
            bundle = builder.build_turn_message(
                inp.message,
                entrypoint="web",
                client_id=inp.client_id,
                user_id=inp.user_id,
                skill_id=effective_skill_id,
                session_messages=_session_messages_for_context(
                    agent,
                    sid,
                    hist_cap,
                ),
                retrieved_context=retrieved_context,
                current_task_summary=current_summary,
                session_task_index=task_index,
                history_max_messages_override=hist_cap,
                auto_retrieve_reason=(
                    retrieve_decision.reason if settings.enable_context_auto_retrieve else None
                ),
                entrypoint_extra_lines=list(_WEB_EXTRA_INSTRUCTIONS),
            )
            run_message = bundle.message
            context_diagnostics = build_context_diagnostics(bundle).to_dict()
            if settings.context_trace_log:
                log_context_management_trace(
                    request_id=rid,
                    session_id=sid,
                    trace=bundle.trace,
                    route="/chat",
                )
        t0 = time.perf_counter()
        out = agent.run(
            run_message,
            session_id=sid,
            user_id=inp.user_id or inp.client_id,
            stream=False,
        )
        log_agent_run_obs(
            request_id=rid,
            session_id=sid,
            out=out,
            elapsed_s=time.perf_counter() - t0,
            route="/chat",
        )
        text, rkind, structured = _format_web_chat_reply(agent, out)
        if task_store is not None and active_task_id is not None:
            task_store.append_message(
                session_id=sid,
                task_id=active_task_id,
                role="assistant",
                content=text,
            )
            if task_summary_service is not None:
                task_summary_service.maybe_update(session_id=sid, task_id=active_task_id)
        tr = _transcripts.setdefault(sid, [])
        tr.append(("user", inp.message))
        tr.append(("assistant", text))
        trace = _serialize_run_trace(out) if inp.include_trace else None
        if trace is not None and context_diagnostics is not None:
            trace["context_diagnostics"] = context_diagnostics
        history = [ChatHistoryTurn(role=r, content=c) for r, c in tr]
        return ChatOut(
            reply=text,
            session_id=sid,
            use_slow_reasoning_applied=slow_applied,
            trace=trace,
            history=history,
            reply_content_kind=rkind,
            structured=structured,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/ingest")
def http_ingest_v1(inp: IngestV1In, request: Request):
    """
    P2-6：统一显式 **target** 入口；成功时返回 200 与 ``status``/写入摘要。
    部署在生产前**必须**由网关做鉴权与限流；本进程默认**不**校验密钥。
    """
    settings = Settings.from_env()
    eff_skill = (inp.skill_id or "").strip() or settings.default_skill_id
    slow_applied = _resolve_use_slow(inp.use_slow_reasoning)
    _, ctrl, _ = _get_bundle_for(inp.client_id, inp.user_id, slow_applied, skill_id=inp.skill_id)
    rid = getattr(request.state, "request_id", "-")
    _web_log.info(
        "AGENT_OS_OBS route=/ingest request_id=%s target=%s client_id=%s",
        rid,
        inp.target,
        inp.client_id,
    )
    try:
        return run_ingest_v1(
            target=inp.target,
            text=inp.text,
            client_id=inp.client_id,
            user_id=inp.user_id,
            skill_id=eff_skill,
            settings=settings,
            controller=ctrl,
            mem_kind=inp.mem_kind,
            task_id=inp.task_id,
            supersedes_event_id=inp.supersedes_event_id,
            weight_count=inp.weight_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/memory/profile/list")
def memory_profile_list(
    client_id: str = Query(..., min_length=1, max_length=256),
    user_id: str | None = None,
    use_slow: bool | None = Query(None),
    skill_id: str | None = Query(None, max_length=256),
):
    """列出画像层记忆：本地 JSON 带 index 可删；Mem0 仅搜索展示。"""
    settings, ctrl, _ = _get_bundle_for(client_id, user_id, use_slow, skill_id=skill_id)
    uid = _mem_uid(client_id, user_id)
    if settings.mem0_api_key:
        hits = ctrl.search_profile("", client_id=client_id, user_id=user_id, limit=100)
        return {
            "backend": "mem0",
            "user_key": uid,
            "items": [
                {"text": h.text, "metadata": getattr(h, "metadata", None) or {}} for h in hits
            ],
            "delete_note": "Mem0 托管记忆请在 Mem0 控制台或官方 API 删除；本演示不提供云端删除。",
        }
    path = _resolve_under_agent_os(settings.local_memory_path)
    if not path.exists():
        return {"backend": "local", "user_key": uid, "items": [], "path": str(path)}
    data = _load_local_memory_json(path)
    raw_bucket = data.get("users", {}).get(uid, {})
    bucket = raw_bucket.get("memories", []) if isinstance(raw_bucket, dict) else []
    if not isinstance(bucket, list):
        bucket = []
    items = [
        {"index": i, "text": m.get("text", ""), "metadata": m.get("metadata") or {}}
        for i, m in enumerate(bucket)
        if isinstance(m, dict)
    ]
    return {"backend": "local", "user_key": uid, "items": items, "path": str(path)}


@app.post("/api/memory/profile/delete-local")
def memory_profile_delete_local(inp: ProfileDeleteLocalIn):
    settings, _, _ = _get_bundle_for(
        inp.client_id, inp.user_id, inp.use_slow_reasoning, skill_id=inp.skill_id
    )
    if settings.mem0_api_key:
        raise HTTPException(501, detail="当前为 Mem0 云端后端，删除请使用 Mem0 控制台")
    uid = _mem_uid(inp.client_id, inp.user_id)
    path = _resolve_under_agent_os(settings.local_memory_path)
    if not path.exists():
        raise HTTPException(404, detail="本地记忆文件不存在")
    data = _load_local_memory_json(path)
    users = data.setdefault("users", {})
    bucket = users.setdefault(uid, {"memories": []})
    mems = bucket.get("memories", []) if isinstance(bucket, dict) else []
    if not isinstance(mems, list):
        mems = []
        if isinstance(bucket, dict):
            bucket["memories"] = mems
    if inp.index < 0 or inp.index >= len(mems):
        raise HTTPException(400, detail="index 越界")
    mems.pop(inp.index)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "remaining": len(mems)}


@app.get("/api/memory/hindsight/list")
def memory_hindsight_list(
    client_id: str = Query(..., min_length=1, max_length=256),
    use_slow: bool | None = Query(None),
    skill_id: str | None = Query(None, max_length=256),
):
    settings, _, _ = _get_bundle_for(client_id, None, use_slow, skill_id=skill_id)
    path = _resolve_under_agent_os(settings.hindsight_path)
    items: list[dict[str, Any]] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for i, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("client_id") != client_id:
                continue
            items.append({"file_line": i, "row": row})
    return {"items": items, "path": str(path)}


@app.get("/api/memory/hindsight/search")
def memory_hindsight_search(
    request: Request,
    client_id: str = Query(..., min_length=1, max_length=256),
    query: str = Query("", max_length=8192),
    user_id: str | None = None,
    use_slow: bool | None = Query(None),
    skill_id: str | None = Query(None, max_length=256),
    task_id: str | None = Query(None, max_length=256),
    deliverable_type: str | None = Query(None, max_length=256),
    limit: int = Query(8, ge=1, le=50),
    debug_scores: bool = Query(False),
):
    """受控检索 Hindsight；debug_scores 仅用于排查召回排序，不应默认展示给最终用户。"""
    if debug_scores:
        _assert_admin_request_allowed(request)
    _, ctrl, _ = _get_bundle_for(client_id, user_id, use_slow, skill_id=skill_id)
    lines = ctrl.search_hindsight(
        query,
        client_id=client_id,
        limit=limit,
        user_id=user_id,
        task_id=task_id,
        skill_id=skill_id,
        deliverable_type=deliverable_type,
        debug_scores=debug_scores,
    )
    return {
        "items": lines,
        "count": len(lines),
        "debug_scores": bool(debug_scores),
    }


@app.post("/api/memory/hindsight/delete-line")
def memory_hindsight_delete(inp: HindsightDeleteIn):
    settings, _, _ = _get_bundle_for(
        inp.client_id, None, inp.use_slow_reasoning, skill_id=inp.skill_id
    )
    path = _resolve_under_agent_os(settings.hindsight_path)
    result = HindsightStore(
        path,
        enable_vector_recall=True,
        vector_index_path=settings.hindsight_vector_index_path,
    ).delete_line(file_line=inp.file_line, expected_client_id=inp.client_id)
    if result.get("status") == "missing_source":
        raise HTTPException(404, detail="hindsight 文件不存在")
    if result.get("status") == "forbidden":
        raise HTTPException(403, detail="该行的 client_id 与请求不一致，禁止删除")
    if result.get("reason") == "read_failed":
        raise HTTPException(400, detail=f"hindsight 文件无法读取: {result.get('error')}")
    if result.get("status") != "ok":
        raise HTTPException(400, detail=result)
    return result


@app.post("/api/memory/ingest", response_model=MemoryIngestOut)
def memory_ingest(inp: MemoryIngestIn):
    k = inp.kind.strip().lower()
    text = inp.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    if k == "fact":
        lane = MemoryLane.ATTRIBUTE
        fact_type: Any = "attribute"
    elif k == "preference":
        lane = MemoryLane.ATTRIBUTE
        fact_type = "preference"
    elif k == "feedback":
        lane = MemoryLane.TASK_FEEDBACK
        fact_type = "feedback"
    else:
        raise HTTPException(status_code=400, detail="kind 须为 fact | preference | feedback")

    _, ctrl, _ = _get_bundle_for(
        inp.client_id, inp.user_id, inp.use_slow_reasoning, skill_id=inp.skill_id
    )
    fact = UserFact(
        lane=lane,
        client_id=inp.client_id,
        user_id=inp.user_id,
        text=text,
        fact_type=fact_type,
        task_id=inp.task_id if k == "feedback" else None,
    )
    try:
        r = ctrl.ingest_user_fact(fact)
        return MemoryIngestOut(
            status="ok",
            written_to=list(r.written_to),
            dedup_skipped=r.dedup_skipped,
            detail=r.dedup_reason,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/session/end", response_model=SessionEndOut)
def session_end(inp: SessionEndIn):
    sid = inp.session_id.strip()
    transcript = _transcripts.pop(sid, [])

    _, ctrl, _ = _get_bundle_for(
        inp.client_id, inp.user_id, inp.use_slow_reasoning, skill_id=inp.skill_id
    )
    review_status: str | None = None

    if inp.run_review:
        if ctrl.hindsight_store is None:
            review_status = "skipped: hindsight_store missing"
        elif not transcript:
            review_status = "skipped: empty transcript"
        else:
            review = AsyncReviewService.from_env(ctrl)
            result = review.submit_and_wait(
                client_id=inp.client_id,
                user_id=inp.user_id,
                task_id=inp.task_id,
                transcript=transcript,
            )
            review_status = str(result.get("status") or "unknown")
    else:
        review_status = "skipped: run_review false"

    return SessionEndOut(
        status="ok",
        review=review_status,
        transcript_turns=len(transcript) // 2,
    )


def _web_port_from_env() -> int:
    raw = (os.getenv("AGENT_OS_WEB_PORT") or "8765").strip()
    try:
        return int(raw)
    except ValueError:
        _web_log.warning("AGENT_OS_WEB_PORT=%r 无法解析，回退为 8765", raw)
        return 8765


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("AGENT_OS_WEB_HOST", "127.0.0.1")
    port = _web_port_from_env()
    uvicorn.run(app, host=host, port=port)
