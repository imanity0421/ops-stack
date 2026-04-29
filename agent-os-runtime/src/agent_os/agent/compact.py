from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from agent_os.agent.task_memory import TaskMemoryStore, TaskMessage, _iso

COMPACT_SCHEMA_VERSION = "v1"
COMPACT_POLICY_VERSION = "compact_summary_v1"


class CompactSummaryCore(BaseModel):
    current_artifact_refs: list[str] = Field(default_factory=list)
    pinned_refs: list[str] = Field(default_factory=list)
    goal: str = ""
    constraints: list[str] = Field(default_factory=list)
    progress: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    last_user_instruction: str = ""
    open_questions: list[str] = Field(default_factory=list)


class CompactSummary(BaseModel):
    schema_version: Literal["v1"] = "v1"
    core: CompactSummaryCore
    business_writing_pack: dict[str, Any] | None = None
    skill_state: dict[str, Any] | None = None


class SkillSchemaProvider(Protocol):
    def get_compact_schema_fragment(self) -> type[BaseModel]:
        ...


@dataclass(frozen=True)
class CompactSummaryRecord:
    session_id: str
    task_id: str
    summary_version: int
    summary: CompactSummary
    covered_message_count: int
    updated_at: str
    compact_model: str = "fallback"
    compact_policy_version: str = COMPACT_POLICY_VERSION
    status: Literal["active", "stale"] = "active"
    covered_message_start_id: str | None = None
    covered_message_end_id: str | None = None

    @property
    def schema_version(self) -> str:
        return self.summary.schema_version

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "summary_version": self.summary_version,
            "schema_version": self.schema_version,
            "summary": self.summary.model_dump(mode="json"),
            "covered_message_start_id": self.covered_message_start_id,
            "covered_message_end_id": self.covered_message_end_id,
            "covered_message_count": self.covered_message_count,
            "updated_at": self.updated_at,
            "compact_model": self.compact_model,
            "compact_policy_version": self.compact_policy_version,
            "status": self.status,
        }


def _text_or_empty(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _shorten(text: str, max_chars: int) -> str:
    t = " ".join(_text_or_empty(text).strip().split())
    if max_chars <= 0:
        return ""
    if len(t) <= max_chars:
        return t
    if max_chars <= 3:
        return t[:max_chars]
    return t[: max_chars - 3] + "..."


def _last_user_message(messages: list[TaskMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and _text_or_empty(message.content).strip():
            return _text_or_empty(message.content).strip()
    return ""


def _fallback_compact_summary(
    *,
    messages: list[TaskMessage],
    current_artifact_refs: list[str],
    pinned_refs: list[str],
) -> CompactSummary:
    recent = messages[-12:]
    user_lines = [
        _shorten(message.content, 160)
        for message in recent
        if message.role == "user" and _text_or_empty(message.content).strip()
    ]
    assistant_lines = [
        _shorten(message.content, 160)
        for message in recent
        if message.role == "assistant" and _text_or_empty(message.content).strip()
    ]
    last_user = _last_user_message(messages)
    goal = user_lines[0] if user_lines else "继续推进当前 task"
    constraints = [
        line
        for line in user_lines
        if any(token in line.lower() for token in ("必须", "不要", "不能", "需要", "must", "do not"))
    ][:5]
    return CompactSummary(
        core=CompactSummaryCore(
            current_artifact_refs=current_artifact_refs,
            pinned_refs=pinned_refs,
            goal=_shorten(goal, 240),
            constraints=constraints or ["遵守用户最新明确约束"],
            progress=assistant_lines[:5] or ["已保留最近对话上下文"],
            pending=["继续响应用户最新请求"],
            last_user_instruction=_shorten(last_user, 320),
            open_questions=[],
        )
    )


def _compact_prompt(
    *,
    messages: list[TaskMessage],
    current_artifact_refs: list[str],
    pinned_refs: list[str],
) -> str:
    transcript = "\n".join(
        f"{message.role}: {_shorten(message.content, 1200)}" for message in messages[-30:]
    )
    return (
        "你是 agent-os 的 CompactSummary v1 生成器。"
        "请把当前 task/session 的对话压缩成严格 JSON，不要输出 Markdown。"
        "必须只输出这些字段：schema_version, core, business_writing_pack, skill_state。"
        "core 里 current_artifact_refs / pinned_refs 是 system-state，请原样保留，不要编造 ID。"
        "business_writing_pack 与 skill_state 在 Stage 3 先输出 null。\n\n"
        f"current_artifact_refs={json.dumps(current_artifact_refs, ensure_ascii=False)}\n"
        f"pinned_refs={json.dumps(pinned_refs, ensure_ascii=False)}\n\n"
        f"对话：\n{transcript}"
    )


def _llm_compact_summary(
    *,
    messages: list[TaskMessage],
    current_artifact_refs: list[str],
    pinned_refs: list[str],
    model: str | None,
) -> CompactSummary | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_API_BASE") or None,
        )
        mid = model or os.getenv("AGENT_OS_COMPACT_MODEL") or os.getenv("AGENT_OS_MODEL", "gpt-4o-mini")
        response = client.chat.completions.create(
            model=mid,
            messages=[
                {
                    "role": "user",
                    "content": _compact_prompt(
                        messages=messages,
                        current_artifact_refs=current_artifact_refs,
                        pinned_refs=pinned_refs,
                    ),
                }
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        summary = CompactSummary.model_validate_json(raw)
        return _with_system_state(summary, current_artifact_refs, pinned_refs)
    except Exception:
        return None


def _with_system_state(
    summary: CompactSummary,
    current_artifact_refs: list[str],
    pinned_refs: list[str],
) -> CompactSummary:
    data = summary.model_dump(mode="json")
    core = dict(data.get("core") or {})
    core["current_artifact_refs"] = current_artifact_refs
    core["pinned_refs"] = pinned_refs
    data["core"] = core
    data["business_writing_pack"] = data.get("business_writing_pack")
    data["skill_state"] = data.get("skill_state")
    return CompactSummary.model_validate(data)


class CompactSummaryService:
    def __init__(
        self,
        store: TaskMemoryStore,
        *,
        model: str | None = None,
    ) -> None:
        self._store = store
        self._model = model

    def compact(
        self,
        *,
        session_id: str,
        task_id: str,
        current_artifact_refs: list[str] | None = None,
        pinned_refs: list[str] | None = None,
    ) -> CompactSummaryRecord | None:
        messages = self._store.task_messages(session_id=session_id, task_id=task_id)
        if not messages:
            return None
        current_refs = list(dict.fromkeys(current_artifact_refs or []))
        pinned = list(dict.fromkeys(pinned_refs or []))
        existing = self._store.get_compact_summary(session_id=session_id, task_id=task_id)
        summary = _llm_compact_summary(
            messages=messages,
            current_artifact_refs=current_refs,
            pinned_refs=pinned,
            model=self._model,
        ) or _fallback_compact_summary(
            messages=messages,
            current_artifact_refs=current_refs,
            pinned_refs=pinned,
        )
        record = CompactSummaryRecord(
            session_id=session_id,
            task_id=task_id,
            summary_version=(existing.summary_version + 1) if existing is not None else 1,
            summary=summary,
            covered_message_start_id=messages[0].message_id,
            covered_message_end_id=messages[-1].message_id,
            covered_message_count=len(messages),
            updated_at=_iso(),
            compact_model=self._model or os.getenv("AGENT_OS_COMPACT_MODEL") or "fallback",
        )
        self._store.upsert_compact_summary(record)
        return record


def compact_summary_from_json(raw: str) -> CompactSummary:
    try:
        return CompactSummary.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid compact summary json: {exc}") from exc


def build_compact_summary_instruction(record: CompactSummaryRecord | None) -> str | None:
    if record is None:
        return None
    core = record.summary.core
    lines = [
        "<compact_summary schema_version=\"v1\">",
        "<usage_rule>这是 compact 后的 task 工作面恢复锚点；优先保持 goal / constraints / artifact refs 连贯。</usage_rule>",
        f"<task_id>{record.task_id}</task_id>",
        f"<summary_version>{record.summary_version}</summary_version>",
        f"<covered_message_count>{record.covered_message_count}</covered_message_count>",
        f"<goal>{_shorten(core.goal, 300)}</goal>",
        "<constraints>",
        *[f"- {_shorten(item, 180)}" for item in core.constraints],
        "</constraints>",
        "<progress>",
        *[f"- {_shorten(item, 180)}" for item in core.progress],
        "</progress>",
        "<pending>",
        *[f"- {_shorten(item, 180)}" for item in core.pending],
        "</pending>",
        f"<last_user_instruction>{_shorten(core.last_user_instruction, 300)}</last_user_instruction>",
    ]
    if core.current_artifact_refs:
        lines.append(
            "<current_artifact_refs>"
            + ", ".join(_shorten(ref, 80) for ref in core.current_artifact_refs)
            + "</current_artifact_refs>"
        )
    if core.pinned_refs:
        lines.append(
            "<pinned_refs>" + ", ".join(_shorten(ref, 80) for ref in core.pinned_refs) + "</pinned_refs>"
        )
    lines.append("</compact_summary>")
    return "\n".join(lines)
