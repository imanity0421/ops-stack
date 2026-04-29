from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError, create_model

from agent_os.agent.task_memory import TaskMemoryStore, TaskMessage, _iso

COMPACT_SCHEMA_VERSION = "v2"
COMPACT_POLICY_VERSION = "compact_summary_v2"
SkillFragmentSkipReason = Literal[
    "none",
    "no_active_skill_id",
    "provider_missing",
    "fragment_missing",
]


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
    """CompactSummary v2 (Phase 9): two-layer schema -- core + skill_state.

    v1 had an additional Layer 2 ``business_writing_pack`` field which has been
    removed (see ARCHITECTURE.md Phase 9 revision log + scripts/migrate_compact_v1_to_v2.py).
    """

    schema_version: Literal["v2"] = "v2"
    core: CompactSummaryCore
    skill_state: dict[str, Any] | None = None


class SkillSchemaProvider(Protocol):
    def get_compact_schema_fragment(self) -> type[BaseModel] | None:
        ...


class SkillSchemaRegistry(Protocol):
    def get_schema_fragment(self, skill_id: str) -> type[BaseModel] | None:
        ...


@dataclass(frozen=True)
class SkillFragmentResolution:
    active_skill_id: str | None
    skill_state_schema: type[BaseModel] | None
    skill_fragment_skipped: bool
    skill_fragment_skip_reason: SkillFragmentSkipReason = "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "active_skill_id": self.active_skill_id,
            "skill_fragment_skipped": self.skill_fragment_skipped,
            "skill_fragment_skip_reason": self.skill_fragment_skip_reason,
            "skill_state_schema": self.skill_state_schema.__name__
            if self.skill_state_schema is not None
            else None,
        }


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


def compose_compact_summary_schema(
    skill_state_schema: type[BaseModel] | None = None,
) -> type[BaseModel]:
    """Compose CTE-owned core with an optional SR-owned skill_state schema."""

    if skill_state_schema is None:
        return CompactSummary
    return create_model(
        "ComposedCompactSummary",
        __base__=BaseModel,
        schema_version=(Literal["v2"], "v2"),
        core=(CompactSummaryCore, ...),
        skill_state=(skill_state_schema | None, None),
    )


def resolve_skill_schema_fragment(
    *,
    active_skill_id: str | None = None,
    skill_schema_provider: SkillSchemaProvider | None = None,
    skill_schema_registry: SkillSchemaRegistry | None = None,
) -> SkillFragmentResolution:
    skill_id = (active_skill_id or "").strip() or None
    if skill_schema_provider is not None:
        fragment = skill_schema_provider.get_compact_schema_fragment()
        if fragment is None:
            return SkillFragmentResolution(
                active_skill_id=skill_id,
                skill_state_schema=None,
                skill_fragment_skipped=True,
                skill_fragment_skip_reason="fragment_missing",
            )
        return SkillFragmentResolution(
            active_skill_id=skill_id,
            skill_state_schema=fragment,
            skill_fragment_skipped=False,
        )
    if skill_id is None:
        return SkillFragmentResolution(
            active_skill_id=None,
            skill_state_schema=None,
            skill_fragment_skipped=True,
            skill_fragment_skip_reason="no_active_skill_id",
        )
    if skill_schema_registry is None:
        return SkillFragmentResolution(
            active_skill_id=skill_id,
            skill_state_schema=None,
            skill_fragment_skipped=True,
            skill_fragment_skip_reason="provider_missing",
        )
    fragment = skill_schema_registry.get_schema_fragment(skill_id)
    if fragment is None:
        return SkillFragmentResolution(
            active_skill_id=skill_id,
            skill_state_schema=None,
            skill_fragment_skipped=True,
            skill_fragment_skip_reason="provider_missing",
        )
    return SkillFragmentResolution(
        active_skill_id=skill_id,
        skill_state_schema=fragment,
        skill_fragment_skipped=False,
    )


def _to_compact_summary(summary: BaseModel | CompactSummary) -> CompactSummary:
    data = summary.model_dump(mode="json")
    skill_state = data.get("skill_state")
    if isinstance(skill_state, BaseModel):
        data["skill_state"] = skill_state.model_dump(mode="json")
    return CompactSummary.model_validate(data)


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
    response_schema: type[BaseModel] | None = None,
) -> str:
    transcript = "\n".join(
        f"{message.role}: {_shorten(message.content, 1200)}" for message in messages[-30:]
    )
    schema_hint = ""
    if response_schema is not None:
        schema_hint = (
            "\n\n完整 JSON Schema（用于约束输出结构；不要把 schema 本身写入输出）：\n"
            + json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        )
    return (
        "你是 agent-os 的 CompactSummary v2 生成器。"
        "请把当前 task/session 的对话压缩成严格 JSON，不要输出 Markdown。"
        "必须只输出这些字段：schema_version, core, skill_state。"
        "core 里 current_artifact_refs / pinned_refs 是 system-state，请原样保留，不要编造 ID。"
        "skill_state 由 active skill 的 SR 自解析；当前若无 active skill 请输出 null。\n\n"
        f"current_artifact_refs={json.dumps(current_artifact_refs, ensure_ascii=False)}\n"
        f"pinned_refs={json.dumps(pinned_refs, ensure_ascii=False)}\n\n"
        f"对话：\n{transcript}"
        f"{schema_hint}"
    )


def _llm_compact_summary(
    *,
    messages: list[TaskMessage],
    current_artifact_refs: list[str],
    pinned_refs: list[str],
    model: str | None,
    response_schema: type[BaseModel],
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
                        response_schema=response_schema,
                    ),
                }
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        summary = _to_compact_summary(response_schema.model_validate_json(raw))
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
    data["skill_state"] = data.get("skill_state")
    return CompactSummary.model_validate(data)


class CompactSummaryService:
    def __init__(
        self,
        store: TaskMemoryStore,
        *,
        model: str | None = None,
        skill_schema_provider: SkillSchemaProvider | None = None,
        skill_schema_registry: SkillSchemaRegistry | None = None,
        active_skill_id: str | None = None,
    ) -> None:
        self._store = store
        self._model = model
        self._skill_schema_provider = skill_schema_provider
        self._skill_schema_registry = skill_schema_registry
        self._active_skill_id = active_skill_id

    def skill_fragment_resolution(self) -> SkillFragmentResolution:
        return resolve_skill_schema_fragment(
            active_skill_id=self._active_skill_id,
            skill_schema_provider=self._skill_schema_provider,
            skill_schema_registry=self._skill_schema_registry,
        )

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
        resolution = self.skill_fragment_resolution()
        response_schema = compose_compact_summary_schema(resolution.skill_state_schema)
        summary = _llm_compact_summary(
            messages=messages,
            current_artifact_refs=current_refs,
            pinned_refs=pinned,
            model=self._model,
            response_schema=response_schema,
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
    """Deserialize a CompactSummary JSON blob with v1 -> v2 inline migration.

    v1 had ``schema_version="v1"`` and an extra ``business_writing_pack`` key.
    Phase 9 collapsed schema to two layers; this loader transparently drops
    ``business_writing_pack`` and bumps ``schema_version`` so that previously
    stored v1 blobs (written by Stage 3 / Stage 4 code) keep deserializing
    without forcing a hard offline migration. The offline migration script
    (``scripts/migrate_compact_v1_to_v2.py``) is still preferred for SQLite
    columns -- this fallback exists for resilience, not as primary path.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid compact summary json: {exc}") from exc
    if isinstance(data, dict) and data.get("schema_version") == "v1":
        data.pop("business_writing_pack", None)
        data["schema_version"] = "v2"
    try:
        return CompactSummary.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid compact summary json: {exc}") from exc


def build_compact_summary_instruction(record: CompactSummaryRecord | None) -> str | None:
    if record is None:
        return None
    core = record.summary.core
    lines = [
        "<compact_summary schema_version=\"v2\">",
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
