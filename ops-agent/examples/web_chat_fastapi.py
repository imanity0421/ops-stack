#!/usr/bin/env python3
"""
浏览器中试用 ops-agent：FastAPI + 多轮 chat、手动记忆写入、结束对话与可选 Hindsight 复盘。

依赖（在 ops-agent 目录、已 activate venv）：
  pip install fastapi "uvicorn[standard]"

运行（在 ops-agent 目录，已配置 .env）：
  python examples/web_chat_fastapi.py

修改本文件或 `ops_agent` 源码后须 **停止进程（Ctrl+C）再重新启动** 并刷新浏览器；未加 `--reload` 时不会自动重载。

浏览器打开：对话 `/` ，记忆 `/memory` ，**流程与 Prompt** `/debug`（当前 Agent 指令与工具，供调试）。

说明：
- **本 Web 进程内**：模型 **不会** 获得 `record_client_fact` / `record_client_preference` / `record_task_feedback` 三个工具；写入 **仅** 能通过页面按钮（或 `POST /api/memory/ingest`）。**Hindsight 的 lesson 复盘** 仅能通过「结束对话」且勾选「进行复盘」（或 `POST /api/session/end` 且 `run_review: true`）。
- 终端 CLI `python -m ops_agent` 仍为完整工具集，不受影响。
- **0.5+**：三页统一 Demo 样式；**`/debug`** 展示当前租户绑定的 Agent 配置、指令栈与工具列表（`GET /api/agent/inspect`）。身份支持 **client_id / user_id** 多组预设（localStorage）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ops_agent.agent.factory import get_agent, new_session_id
from ops_agent.config import Settings
from ops_agent.manifest_loader import load_agent_manifest
from ops_agent.knowledge.graphiti_reader import GraphitiReadService
from ops_agent.memory.controller import MemoryController
from ops_agent.memory.models import MemoryLane, UserFact
from ops_agent.review.async_review import AsyncReviewService

# Web 演示：从 Agent 工具列表移除三项，仅保留页面 / API 手动写入
_WEB_EXCLUDED_MEMORY_WRITE_TOOLS = frozenset(
    {"record_client_fact", "record_client_preference", "record_task_feedback"}
)
_WEB_EXTRA_INSTRUCTIONS = [
    "【Web 演示】record_client_fact / record_client_preference / record_task_feedback 已关闭，"
    "请勿在回复中假装已写入记忆。事实、偏好、任务反馈请用户通过页面「手动写入记忆」提交；"
    "复盘 lesson 仅能通过「结束对话」并勾选「进行复盘」。",
]

# ---------- 按 (client_id, user_id) 缓存 Agent，保证工具绑定的租户一致 ----------
_bundles: dict[tuple[str, str], tuple[Settings, MemoryController, Any]] = {}
_no_knowledge = os.getenv("OPS_WEB_NO_KNOWLEDGE", "1").strip() in ("1", "true", "yes")
_persona = os.getenv("OPS_AGENT_PERSONA") or None
_slow = os.getenv("OPS_WEB_SLOW", "").strip() in ("1", "true", "yes")

# session_id -> (user, assistant) 轮次列表
_transcripts: dict[str, list[tuple[str, str]]] = {}
def _bundle_key(client_id: str, user_id: str | None) -> tuple[str, str]:
    return (client_id, user_id or "")


def _build_stack(
    *,
    client_id: str,
    user_id: str | None,
    no_knowledge: bool,
    persona: str | None,
    slow: bool,
):
    settings = Settings.from_env()
    ctrl = MemoryController.create_default(
        mem0_api_key=settings.mem0_api_key,
        mem0_host=settings.mem0_host,
        local_memory_path=settings.local_memory_path,
        hindsight_path=settings.hindsight_path,
        snapshot_every_n_turns=settings.snapshot_every_n_turns,
    )
    knowledge = None if no_knowledge else GraphitiReadService.from_env(settings.knowledge_fallback_path)
    eff_persona = persona if persona is not None else settings.agent_persona
    agent = get_agent(
        ctrl,
        client_id=client_id,
        user_id=user_id,
        thought_mode="slow" if slow else "fast",
        knowledge=knowledge,
        settings=settings,
        persona=eff_persona,
        exclude_tool_names=set(_WEB_EXCLUDED_MEMORY_WRITE_TOOLS),
        extra_instructions=list(_WEB_EXTRA_INSTRUCTIONS),
    )
    return settings, ctrl, agent


def _get_bundle_for(client_id: str, user_id: str | None) -> tuple[Settings, MemoryController, Any]:
    k = _bundle_key(client_id, user_id)
    if k not in _bundles:
        _bundles[k] = _build_stack(
            client_id=client_id,
            user_id=user_id,
            no_knowledge=_no_knowledge,
            persona=_persona,
            slow=_slow,
        )
    return _bundles[k]


def _mem_uid(client_id: str, user_id: str | None) -> str:
    return f"{client_id}::{user_id}" if user_id else client_id


def _resolve_under_ops_agent(p: Path) -> Path:
    """Settings 里相对路径相对 ops-agent 根目录。"""
    if p.is_absolute():
        return p
    return (_ROOT / p).resolve()


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


def _agent_inspect_payload(client_id: str, user_id: str | None) -> dict[str, Any]:
    """当前 Web 进程内、与对话一致的 Agent 实例：指令栈、工具名、Manifest 与路径（供 /debug 调试）。"""
    settings, _ctrl, agent = _get_bundle_for(client_id, user_id)
    manifest = load_agent_manifest(settings.agent_manifest_path)
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
            "agent_manifest": str(settings.agent_manifest_path) if settings.agent_manifest_path else None,
            "handoff": str(settings.handoff_manifest_path) if settings.handoff_manifest_path else None,
            "golden_rules": str(settings.golden_rules_path) if settings.golden_rules_path else None,
            "knowledge_fallback": str(settings.knowledge_fallback_path) if settings.knowledge_fallback_path else None,
            "local_memory": str(_resolve_under_ops_agent(settings.local_memory_path)),
            "hindsight": str(_resolve_under_ops_agent(settings.hindsight_path)),
        },
        "env_flags": {
            "OPS_WEB_NO_KNOWLEDGE": _no_knowledge,
            "OPS_WEB_SLOW": _slow,
            "OPS_AGENT_PERSONA": _persona or settings.agent_persona,
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


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    client_id: str = "demo_client"
    user_id: str | None = None
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
    trace: dict[str, Any] | None = None
    history: list[ChatHistoryTurn] = Field(
        default_factory=list,
        description="本 session 至今全部轮次，便于前端像聊天应用一样展示",
    )


class MemoryIngestIn(BaseModel):
    """与 record_client_fact / record_client_preference / record_task_feedback 等价写入。"""

    client_id: str = Field(default="demo_client", min_length=1)
    user_id: str | None = None
    text: str = Field(..., min_length=1)
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
    task_id: str | None = None
    run_review: bool = False


class SessionEndOut(BaseModel):
    status: str
    review: str | None = None
    transcript_turns: int = 0


class ProfileDeleteLocalIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    user_id: str | None = None
    index: int = Field(..., ge=0, description="local_memory.json 内该用户 memories 数组下标")


class HindsightDeleteIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    file_line: int = Field(..., ge=1, description="hindsight.jsonl 中的物理行号（自列表接口返回）")


app = FastAPI(title="ops-agent web demo", version="0.5.0")


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


def _identity_block(default_cid: str, *, page_note: str, open_default: bool = False) -> str:
    open_attr = " open" if open_default else ""
    return f"""
<details class="identity-fold"{open_attr}>
<summary class="identity-sum">
  <span class="identity-sum-title">身份与切换</span>
  <span class="identity-preview" id="identityPreview">{default_cid}</span>
</summary>
<div class="identity-inner">
  <p class="identity-hint">{page_note}</p>
  <div class="identity-toolbar">
    <label class="lbl-preset">预设 <select id="presetSel" class="sel-compact"></select></label>
    <button type="button" class="btn-sm" id="btnApplyPreset" title="切换到所选预设">切换</button>
    <button type="button" class="btn-sm" id="btnSavePreset" title="将当前 client_id / user_id 存为预设">保存</button>
    <button type="button" class="btn-sm" id="btnDelPreset" title="删除所选预设">删除</button>
  </div>
  <div class="identity-grid">
    <label>client_id<input id="cid" type="text" value="{default_cid}" autocomplete="off" spellcheck="false"/></label>
    <label>user_id<input id="uid" type="text" placeholder="可选" autocomplete="off" spellcheck="false"/></label>
  </div>
  <button type="button" class="btn-sm btn-secondary" id="btnApplyManual">应用当前身份并清空 session</button>
</div>
</details>
"""

# 对话页与记忆页共用：折叠身份区（避免两处样式漂移）
_IDENTITY_CSS = """
.identity-fold{border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc;margin:0 0 0.65rem;padding:0;}
.identity-fold>summary.identity-sum{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:0.5rem;padding:0.4rem 0.65rem;font-size:0.82rem;font-weight:600;color:#475569;user-select:none;}
.identity-fold>summary::-webkit-details-marker{display:none;}
.identity-sum-title::before{content:"▸ ";display:inline-block;transition:transform .15s;}
details.identity-fold[open] .identity-sum-title::before{transform:rotate(90deg);}
.identity-preview{font-family:ui-monospace,monospace;font-weight:400;font-size:0.78rem;color:#64748b;max-width:55%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.identity-inner{padding:0 0.65rem 0.55rem;border-top:1px solid #e2e8f0;}
.identity-hint{font-size:0.72rem;color:#64748b;margin:0.35rem 0;line-height:1.35;}
.identity-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:0.35rem 0.5rem;margin-bottom:0.35rem;}
.lbl-preset{font-size:0.78rem;color:#475569;display:flex;align-items:center;gap:0.35rem;}
.sel-compact{min-width:140px;max-width:min(52vw,280px);padding:0.2rem 0.35rem;font-size:0.8rem;border-radius:4px;border:1px solid #cbd5e1;}
.identity-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.35rem 0.65rem;margin-bottom:0.35rem;}
@media (max-width:520px){.identity-grid{grid-template-columns:1fr;}}
.identity-grid label{display:flex;flex-direction:column;gap:0.12rem;font-size:0.72rem;color:#475569;font-weight:500;}
.identity-grid input{padding:0.3rem 0.45rem;font-size:0.82rem;border:1px solid #cbd5e1;border-radius:4px;}
.btn-sm{padding:0.28rem 0.55rem;font-size:0.78rem;border-radius:5px;border:1px solid #cbd5e1;background:#fff;}
.btn-sm:hover:not(:disabled){background:#f1f5f9;}
.btn-secondary{width:100%;margin-top:0.15rem;color:#475569;}
"""

# 全站 Demo：与「消息」面板同一视觉层级（panel + 顶栏 + 表格）
_DEMO_BASE_CSS = """
:root{--border:#e2e8f0;--muted:#64748b;--accent:#2563eb;--surface:#f8fafc;}
body{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:0.75rem 1rem 2rem;line-height:1.45;color:#1e293b;background:#fff;}
textarea,input[type=text]{width:100%;max-width:100%;box-sizing:border-box;font:inherit;}
button{font:inherit;cursor:pointer;border-radius:6px;border:1px solid #cbd5e1;background:#fff;}
button:disabled{opacity:0.55;cursor:not-allowed;}
button:focus-visible,summary:focus-visible,textarea:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.page-head{display:flex;align-items:baseline;justify-content:space-between;gap:0.75rem;flex-wrap:wrap;margin-bottom:0.35rem;}
.page-title{font-size:1.35rem;font-weight:700;margin:0;letter-spacing:-0.02em;}
.topnav{font-size:0.9rem;padding:0.35rem 0;margin-bottom:0.25rem;border-bottom:1px solid var(--border);}
.topnav a{text-decoration:none;color:#334155;}
.topnav a:hover{color:var(--accent);}
.topnav .sep{margin:0 0.45rem;color:#94a3b8;}
.hint{font-size:0.85rem;color:#475569;margin:0.25rem 0;line-height:1.4;}
.hint a{color:var(--accent);}
.row{display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center;margin:0.35rem 0;}
label.chk{display:inline-flex;align-items:center;gap:0.35rem;font-size:0.88rem;}
.demo-panel{border:2px solid #bfdbfe;background:linear-gradient(180deg,#f0f9ff 0%,#fff 12%);padding:0.85rem 1rem;margin:0.75rem 0;border-radius:10px;box-shadow:0 1px 3px rgba(15,23,42,.06);}
.demo-panel > legend{font-size:1.02rem;font-weight:700;padding:0 0.35rem;color:#1e3a5f;}
.demo-panel--emerald{border-color:#6ee7b7;background:linear-gradient(180deg,#ecfdf5 0%,#fff 12%);}
.demo-panel--emerald > legend{color:#065f46;}
.demo-panel--violet{border-color:#c4b5fd;background:linear-gradient(180deg,#f5f3ff 0%,#fff 12%);}
.demo-panel--violet > legend{color:#5b21b6;}
.demo-panel--amber{border-color:#fcd34d;background:linear-gradient(180deg,#fffbeb 0%,#fff 12%);}
.demo-panel--amber > legend{color:#92400e;}
.demo-panel--slate{border-color:#94a3b8;background:linear-gradient(180deg,#f1f5f9 0%,#fff 12%);}
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
#thread{border:1px solid var(--border);border-radius:10px;padding:0.75rem;margin:0.5rem 0 0.65rem;background:#fff;min-height:min(28vh,200px);max-height:min(58vh,560px);overflow:auto;display:flex;flex-direction:column;gap:0.65rem;box-shadow:inset 0 1px 2px rgba(15,23,42,.04);}
.msg{max-width:94%;padding:0.5rem 0.75rem;border-radius:12px;font-size:0.92rem;line-height:1.5;}
.msg.user{align-self:flex-end;background:#dbeafe;border:1px solid #93c5fd;}
.msg.assistant{align-self:flex-start;background:#f1f5f9;border:1px solid #e2e8f0;}
.msg .who{font-size:0.68rem;color:#64748b;margin-bottom:0.2rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;}
.msg .body{white-space:pre-wrap;word-break:break-word;}
.chat-meta{display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;margin:0 0 0.5rem;font-size:0.78rem;color:var(--muted);}
.chat-meta .meta-label{font-weight:600;color:#64748b;}
.sid-chip{font-family:ui-monospace,monospace;font-size:0.72rem;border:1px dashed #cbd5e1;background:#fff;padding:0.2rem 0.45rem;border-radius:4px;flex:1;min-width:0;}
.composer{display:flex;gap:0.5rem;align-items:flex-end;margin-top:0.35rem;}
.composer textarea{flex:1;min-height:76px;max-height:200px;resize:vertical;padding:0.55rem 0.65rem;border:1px solid #cbd5e1;border-radius:8px;line-height:1.45;}
.btn-send{padding:0.55rem 1.1rem;font-weight:600;background:var(--accent);color:#fff;border-color:#1d4ed8;white-space:nowrap;align-self:stretch;display:flex;align-items:center;justify-content:center;min-width:5rem;}
.btn-send:hover:not(:disabled){filter:brightness(1.05);}
.composer-hint{font-size:0.72rem;color:var(--muted);margin:0.35rem 0 0;}
#endOut{white-space:pre-wrap;border:1px solid var(--border);padding:0.55rem;margin-top:0.45rem;background:var(--surface);font-size:0.82rem;max-height:160px;overflow:auto;border-radius:6px;}
#thinkOut,#techOut{white-space:pre-wrap;border:1px solid var(--border);padding:0.55rem;margin:0.35rem 0 0;background:#fff;font-family:ui-monospace,monospace;font-size:0.74rem;max-height:220px;overflow:auto;border-radius:6px;}
details.trace{margin-top:0.45rem;}
details.trace>summary{cursor:pointer;font-size:0.82rem;color:#475569;padding:0.25rem 0;}
details.end-fold{border:1px solid var(--border);border-radius:8px;background:var(--surface);margin-top:0.75rem;padding:0;}
details.end-fold>summary{list-style:none;cursor:pointer;padding:0.45rem 0.65rem;font-size:0.85rem;font-weight:600;color:#475569;}
details.end-fold>summary::-webkit-details-marker{display:none;}
.end-inner{padding:0 0.65rem 0.65rem;border-top:1px solid var(--border);}
#btnEnd{padding:0.45rem 0.85rem;}
"""


def _preset_script(default_cid: str) -> str:
    """浏览器端：多组 client_id/user_id 预设（localStorage）。"""
    return f"""
const LS_PRESETS = 'ops_web_identity_presets_v1';
const LS_LAST = 'ops_web_last_identity_v1';
const DEFAULT_CID = {json.dumps(default_cid)};

function defaultPresets() {{
  return [{{ client_id: DEFAULT_CID, user_id: '', label: '默认' }}];
}}
function loadPresets() {{
  try {{
    const raw = localStorage.getItem(LS_PRESETS);
    if (raw) {{
      const a = JSON.parse(raw);
      if (Array.isArray(a) && a.length) return a;
    }}
  }} catch (e) {{}}
  return defaultPresets();
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
function rebuildPresetSelect() {{
  const sel = $('presetSel');
  const arr = loadPresets();
  sel.innerHTML = '';
  arr.forEach((p, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = (p.label || '未命名') + ' — ' + p.client_id + (p.user_id ? ' / ' + p.user_id : '');
    sel.appendChild(o);
  }});
}}
function applyPresetIndex(i) {{
  const arr = loadPresets();
  const p = arr[parseInt(i, 10)];
  if (!p) return;
  $('cid').value = p.client_id || DEFAULT_CID;
  $('uid').value = p.user_id || '';
  if (typeof clearSessionIfAny === 'function') clearSessionIfAny();
  saveLastIdentity();
  updateIdentityPreview();
}}
function initIdentityUI() {{
  rebuildPresetSelect();
  const last = loadLastIdentity();
  if (last && last.client_id) {{
    $('cid').value = last.client_id;
    $('uid').value = last.user_id || '';
  }}
  $('cid').addEventListener('input', () => {{ saveLastIdentity(); updateIdentityPreview(); }});
  $('uid').addEventListener('input', () => {{ saveLastIdentity(); updateIdentityPreview(); }});
  $('btnApplyPreset').onclick = () => applyPresetIndex($('presetSel').value);
  $('btnSavePreset').onclick = () => {{
    const label = prompt('预设名称（便于识别）');
    if (!label) return;
    const arr = loadPresets();
    arr.push({{ client_id: cid(), user_id: uid() || '', label: label.trim() }});
    savePresets(arr);
    rebuildPresetSelect();
    $('presetSel').value = String(arr.length - 1);
  }};
  $('btnDelPreset').onclick = () => {{
    const arr = loadPresets();
    if (arr.length <= 1) {{ alert('至少保留一条预设'); return; }}
    const i = parseInt($('presetSel').value, 10);
    if (!confirm('删除预设 #' + i + ' ?')) return;
    arr.splice(i, 1);
    savePresets(arr);
    rebuildPresetSelect();
    applyPresetIndex(0);
  }};
  $('btnApplyManual').onclick = () => {{
    saveLastIdentity();
    updateIdentityPreview();
    if (typeof clearSessionIfAny === 'function') clearSessionIfAny();
  }};
  updateIdentityPreview();
}}
"""


def _page_chat_html(default_cid: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>ops-agent · 对话</title>
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
{_identity_block(default_cid, page_note="与 session 绑定；切换身份会清空本页 session。")}

<fieldset class="demo-panel">
<legend>消息</legend>
<div class="chat-meta">
  <span class="meta-label">Session</span>
  <input id="sid" class="sid-chip" type="text" readonly placeholder="发送首条消息后生成" title="当前多轮会话 ID"/>
</div>
<div id="thread"></div>
<div class="composer">
  <textarea id="msg" placeholder="输入消息…（Enter 发送，Shift+Enter 换行）" rows="3" autocomplete="off"></textarea>
  <button type="button" class="btn-send" id="go">发送</button>
</div>
<p class="composer-hint">上方为<strong>本会话全部轮次</strong>（同一 Session 内累计）。调试指令栈与工具见 <a href="/debug">流程与 Prompt</a>。</p>
<details class="think trace">
<summary><strong>思考过程</strong> · reasoning</summary>
<p class="hint" style="font-size:0.78rem">若为空：可设 <code>OPS_WEB_SLOW=1</code>。工具参数见下一栏。</p>
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

{_preset_script(default_cid)}

const cid = () => $('cid').value.trim() || DEFAULT_CID;
const uid = () => {{ const v = $('uid').value.trim(); return v || null; }};
const sid = () => $('sid').value.trim();

function renderThread(history) {{
  const el = $('thread');
  el.innerHTML = '';
  if (!history || !history.length) {{
    const p = document.createElement('p');
    p.className = 'hint';
    p.style.margin = '0';
    p.textContent = '（尚无消息，发送后开始多轮对话）';
    el.appendChild(p);
    return;
  }}
  history.forEach((turn) => {{
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
    : '（本步无 reasoning_content / reasoning_steps。开启 OPS_WEB_SLOW=1 且模型支持「思考」后，此处可出现类似 DeepSeek / Gemini 的思考文本；工具参数见下一栏。）';
  const tech = {{ ...t }};
  delete tech.reasoning_content;
  delete tech.reasoning_steps;
  const keys = Object.keys(tech).filter((k) => tech[k] != null && tech[k] !== '');
  techEl.textContent = keys.length ? JSON.stringify(tech, null, 2) : '（无 tools/metrics/events 等）';
}}

function clearSessionIfAny() {{
  $('sid').value = '';
  renderThread([]);
  $('thinkOut').textContent = '';
  $('techOut').textContent = '';
}}

initIdentityUI();
renderThread([]);

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
      include_trace: true,
    }});
    $('sid').value = j.session_id;
    if (j.history && j.history.length) renderThread(j.history);
    else renderThread([{{ role: 'user', content: message }}, {{ role: 'assistant', content: j.reply }}]);
    renderTrace(j.trace);
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
      task_id: $('taskIdEnd').value.trim() || null,
      run_review: $('chkReview').checked,
    }});
    $('endOut').textContent = JSON.stringify(j, null, 2);
    $('sid').value = '';
    $('chkReview').checked = false;
    renderThread([]);
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
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>ops-agent · 记忆管理</title>
<style>
{_DEMO_BASE_CSS}
{_IDENTITY_CSS}
textarea{{min-height:72px;}}
</style></head>
<body>
{_nav_html("memory")}
<h1 class="page-title">记忆管理</h1>
<p class="hint">列表与写入均针对下方 <strong>当前身份</strong>。结束对话与复盘请在 <a href="/">对话页</a>；Agent 指令与工具见 <a href="/debug">流程与 Prompt</a>。</p>
{_identity_block(default_cid, page_note="切换身份后请「刷新」下方列表以查看对应租户数据。", open_default=True)}

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
    const j = await getq('/api/memory/profile/list?client_id=' + encodeURIComponent(cid()) + qUser());
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
          await post('/api/memory/profile/delete-local', {{ client_id: cid(), user_id: uid(), index: parseInt(btn.dataset.idx,10) }});
          await refreshProfile();
        }} catch (e) {{ alert(e); }}
      }};
    }});
  }} catch (e) {{ $('memList').textContent = String(e); }}
}}

async function refreshHind() {{
  $('hindList').textContent = '…';
  try {{
    const j = await getq('/api/memory/hindsight/list?client_id=' + encodeURIComponent(cid()));
    if (!j.items || !j.items.length) {{ $('hindList').textContent = '（无条目）'; return; }}
    $('hindList').innerHTML = '<table class="memtbl"><tr><th>行号</th><th>内容</th><th></th></tr>' +
      j.items.map(it => '<tr><td>' + it.file_line + '</td><td>' + esc(JSON.stringify(it.row)) + '</td><td><button type="button" class="delHind" data-line="' + it.file_line + '">删除</button></td></tr>').join('') + '</table>';
    document.querySelectorAll('.delHind').forEach(btn => {{
      btn.onclick = async () => {{
        if (!confirm('删除 hindsight 第 ' + btn.dataset.line + ' 行？')) return;
        try {{
          await post('/api/memory/hindsight/delete-line', {{ client_id: cid(), file_line: parseInt(btn.dataset.line,10) }});
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
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>ops-agent · 流程与 Prompt</title>
<style>
{_DEMO_BASE_CSS}
{_IDENTITY_CSS}
</style></head>
<body>
{_nav_html("debug")}
<h1 class="page-title">流程与 Prompt</h1>
<p class="hint">展示<strong>当前身份</strong>下与 <a href="/">对话页</a>一致的 Agent：运行时元数据、系统指令栈、工具列表及 Manifest。切换身份或改 <code>.env</code> 后请点「刷新」或重启服务。</p>
{_identity_block(default_cid, page_note="与对话共用同一套 Agent 缓存键 (client_id, user_id)。", open_default=False)}

<fieldset class="demo-panel">
<legend>运行时摘要</legend>
<div class="mem-toolbar">
  <button type="button" class="btn-primary" id="btnInspectRefresh">刷新</button>
</div>
<div id="inspectMeta"></div>
</fieldset>

<fieldset class="demo-panel demo-panel--slate">
<legend>工作流（概念）</legend>
<p class="hint">以下为 ops-agent 典型检索顺序与一轮对话的数据流，便于与下方「指令栈 / 工具」对照调试。</p>
<div class="flow-wrap">
  <div class="flow-title">上下文检索顺序（retrieve_ordered_context）</div>
  <div class="flow-row">
    <div class="flow-node flow-node--hi"><strong>①</strong>Mem0 / 本地画像</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node flow-node--hi"><strong>②</strong>Hindsight 教训</div>
    <span class="flow-arrow">→</span>
    <div class="flow-node"><strong>③</strong>领域知识（Graphiti，未配置则提示）</div>
  </div>
</div>
<div class="flow-wrap">
  <div class="flow-title">单次对话</div>
  <div class="flow-col">
    <div class="flow-row">
      <div class="flow-node flow-node--hi"><strong>输入</strong>用户消息 + session</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node flow-node--hi"><strong>Agent</strong>指令栈 + 可选 reasoning（OPS_WEB_SLOW）</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node flow-node--ok"><strong>输出</strong>回复 / 工具调用（对话页展开 trace）</div>
    </div>
  </div>
</div>
</fieldset>

<fieldset class="demo-panel demo-panel--emerald">
<legend>系统指令栈（instructions）</legend>
<p class="hint">顺序与工厂 <code>get_agent</code> 注入一致；含 manifest、人格、handoff、golden、Web 附加说明等。</p>
<ol id="instrList" class="instr-ol"></ol>
</fieldset>

<fieldset class="demo-panel demo-panel--violet">
<legend>工具（当前 Agent 绑定）</legend>
<p class="hint">删除线表示 Web 演示已从 Agent 移除，仍可通过记忆页 / API 写入。</p>
<div id="toolTags" class="tool-tags"></div>
</fieldset>

<fieldset class="demo-panel demo-panel--amber">
<legend>Manifest（OPS_AGENT_MANIFEST_PATH）</legend>
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

{_preset_script(default_cid)}

const cid = () => $('cid').value.trim() || DEFAULT_CID;
const uid = () => {{ const v = $('uid').value.trim(); return v || null; }};

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
    ['Reasoning', j.reasoning === true ? ('是 ' + (j.reasoning_min_steps != null ? '(' + j.reasoning_min_steps + '–' + j.reasoning_max_steps + ' 步)' : '')) : String(j.reasoning)],
    ['Markdown', String(j.markdown)],
    ['Persona', j.env_flags.OPS_AGENT_PERSONA],
    ['OPS_WEB_SLOW', String(j.env_flags.OPS_WEB_SLOW)],
    ['OPS_WEB_NO_KNOWLEDGE', String(j.env_flags.OPS_WEB_NO_KNOWLEDGE)],
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
    const j = await getq('/api/agent/inspect?' + params.toString());
    renderInspect(j);
  }} catch (e) {{
    $('inspectMeta').innerHTML = '<p class="hint" style="color:#b91c1c">' + esc(String(e)) + '</p>';
  }}
}}

initIdentityUI();
$('btnInspectRefresh').onclick = () => {{ saveLastIdentity(); loadInspect(); }};
['cid', 'uid'].forEach((id) => $(id).addEventListener('input', () => loadInspect()));
['btnApplyPreset', 'btnApplyManual'].forEach((id) => $(id).addEventListener('click', () => setTimeout(loadInspect, 0)));
loadInspect();
</script>
</body></html>
"""


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def page_chat():
    default_cid = os.getenv("OPS_WEB_CLIENT_ID", "demo_client")
    return HTMLResponse(_page_chat_html(default_cid))


@app.get("/memory", response_class=HTMLResponse)
def page_memory():
    default_cid = os.getenv("OPS_WEB_CLIENT_ID", "demo_client")
    return HTMLResponse(_page_memory_html(default_cid))


@app.get("/debug", response_class=HTMLResponse)
def page_debug():
    default_cid = os.getenv("OPS_WEB_CLIENT_ID", "demo_client")
    return HTMLResponse(_page_debug_html(default_cid))


@app.get("/api/agent/inspect")
def api_agent_inspect(client_id: str = "demo_client", user_id: str | None = None):
    """返回当前 Web 进程内与对话一致的 Agent 元数据、指令栈与工具名（供 /debug 调试）。"""
    cid = (client_id or "").strip() or "demo_client"
    uid = (user_id or "").strip() or None
    return _agent_inspect_payload(cid, uid)


@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn):
    _, ctrl, agent = _get_bundle_for(inp.client_id, inp.user_id)
    sid = inp.session_id or new_session_id()
    try:
        ctrl.bump_turn_and_maybe_snapshot(inp.client_id, inp.user_id)
        out = agent.run(
            inp.message,
            session_id=sid,
            user_id=inp.user_id or inp.client_id,
            stream=False,
        )
        text = out.content if isinstance(out.content, str) else str(out.content)
        tr = _transcripts.setdefault(sid, [])
        tr.append(("user", inp.message))
        tr.append(("assistant", text))
        trace = _serialize_run_trace(out) if inp.include_trace else None
        history = [ChatHistoryTurn(role=r, content=c) for r, c in tr]
        return ChatOut(reply=text, session_id=sid, trace=trace, history=history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/memory/profile/list")
def memory_profile_list(client_id: str, user_id: str | None = None):
    """列出画像层记忆：本地 JSON 带 index 可删；Mem0 仅搜索展示。"""
    settings, ctrl, _ = _get_bundle_for(client_id, user_id)
    uid = _mem_uid(client_id, user_id)
    if settings.mem0_api_key:
        hits = ctrl.search_profile("", client_id=client_id, user_id=user_id, limit=100)
        return {
            "backend": "mem0",
            "user_key": uid,
            "items": [
                {"text": h.text, "metadata": getattr(h, "metadata", None) or {}}
                for h in hits
            ],
            "delete_note": "Mem0 托管记忆请在 Mem0 控制台或官方 API 删除；本演示不提供云端删除。",
        }
    path = _resolve_under_ops_agent(settings.local_memory_path)
    if not path.exists():
        return {"backend": "local", "user_key": uid, "items": [], "path": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    bucket = data.get("users", {}).get(uid, {}).get("memories", [])
    items = [{"index": i, "text": m.get("text", ""), "metadata": m.get("metadata") or {}} for i, m in enumerate(bucket)]
    return {"backend": "local", "user_key": uid, "items": items, "path": str(path)}


@app.post("/api/memory/profile/delete-local")
def memory_profile_delete_local(inp: ProfileDeleteLocalIn):
    settings, _, _ = _get_bundle_for(inp.client_id, inp.user_id)
    if settings.mem0_api_key:
        raise HTTPException(501, detail="当前为 Mem0 云端后端，删除请使用 Mem0 控制台")
    uid = _mem_uid(inp.client_id, inp.user_id)
    path = _resolve_under_ops_agent(settings.local_memory_path)
    if not path.exists():
        raise HTTPException(404, detail="本地记忆文件不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    users = data.setdefault("users", {})
    bucket = users.setdefault(uid, {"memories": []})
    mems = bucket.get("memories", [])
    if inp.index < 0 or inp.index >= len(mems):
        raise HTTPException(400, detail="index 越界")
    mems.pop(inp.index)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "remaining": len(mems)}


@app.get("/api/memory/hindsight/list")
def memory_hindsight_list(client_id: str):
    settings, _, _ = _get_bundle_for(client_id, None)
    path = _resolve_under_ops_agent(settings.hindsight_path)
    items: list[dict[str, Any]] = []
    if path.exists():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("client_id") != client_id:
                continue
            items.append({"file_line": i, "row": row})
    return {"items": items, "path": str(path)}


@app.post("/api/memory/hindsight/delete-line")
def memory_hindsight_delete(inp: HindsightDeleteIn):
    settings, _, _ = _get_bundle_for(inp.client_id, None)
    path = _resolve_under_ops_agent(settings.hindsight_path)
    if not path.exists():
        raise HTTPException(404, detail="hindsight 文件不存在")
    lines = path.read_text(encoding="utf-8").splitlines()
    if inp.file_line < 1 or inp.file_line > len(lines):
        raise HTTPException(400, detail="file_line 越界")
    raw = lines[inp.file_line - 1].strip()
    try:
        row = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, detail=f"该行不是合法 JSON: {e}") from e
    if row.get("client_id") != inp.client_id:
        raise HTTPException(403, detail="该行的 client_id 与请求不一致，禁止删除")
    del lines[inp.file_line - 1]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"status": "ok"}


@app.post("/api/memory/ingest", response_model=MemoryIngestOut)
def memory_ingest(inp: MemoryIngestIn):
    k = inp.kind.strip().lower()
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

    _, ctrl, _ = _get_bundle_for(inp.client_id, inp.user_id)
    fact = UserFact(
        lane=lane,
        client_id=inp.client_id,
        user_id=inp.user_id,
        text=inp.text.strip(),
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

    _, ctrl, _ = _get_bundle_for(inp.client_id, inp.user_id)
    review_status: str | None = None

    if inp.run_review:
        if ctrl.hindsight_store is None:
            review_status = "skipped: hindsight_store missing"
        elif not transcript:
            review_status = "skipped: empty transcript"
        else:
            review = AsyncReviewService.from_env(ctrl.hindsight_store)
            review.submit_and_wait(
                client_id=inp.client_id,
                user_id=inp.user_id,
                task_id=inp.task_id,
                transcript=transcript,
            )
            review_status = "completed"
    else:
        review_status = "skipped: run_review false"

    return SessionEndOut(
        status="ok",
        review=review_status,
        transcript_turns=len(transcript) // 2,
    )


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("OPS_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("OPS_WEB_PORT", "8765"))
    uvicorn.run(app, host=host, port=port)
