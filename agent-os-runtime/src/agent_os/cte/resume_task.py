from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from typing import Callable, Literal, Protocol

from agent_os.agent.compact import CompactSummaryRecord
from agent_os.agent.task_memory import TaskEntity, TaskMemoryStore, TaskMessage, TaskSession
from agent_os.er.resume_session import ResumeSessionMeta, StartedSession

ResumeMode = Literal["connect", "fork"]
ForceMode = Literal["connect", "fork"] | None
DeliverableFallbackChain = Literal["none", "full", "tail"]


class ArtifactLookupPort(Protocol):
    def get_artifact(self, artifact_id: str) -> object | None:
        ...


@dataclass(frozen=True)
class ResumeDecision:
    connect_or_fork: ResumeMode
    decision_reason: list[str]
    forced_by_flag: bool
    source_session_id: str
    target_session_id: str
    session_age_minutes: float | None = None
    context_usage_ratio: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "connect_or_fork": self.connect_or_fork,
            "decision_reason": list(self.decision_reason),
            "forced_by_flag": self.forced_by_flag,
            "source_session_id": self.source_session_id,
            "target_session_id": self.target_session_id,
            "session_age_minutes": round(self.session_age_minutes, 2)
            if self.session_age_minutes is not None
            else None,
            "context_usage_ratio": round(self.context_usage_ratio, 4)
            if self.context_usage_ratio is not None
            else None,
        }


@dataclass(frozen=True)
class ResumeFinalState:
    task_id: str
    source_session_id: str
    compact_summary: CompactSummaryRecord | None
    tail_messages: list[TaskMessage] = field(default_factory=list)
    current_artifact_refs: list[str] = field(default_factory=list)
    pinned_refs: list[str] = field(default_factory=list)
    current_deliverable_chars: int = 0
    deliverable_inline_level: str = "none"
    voice_pack_skipped: bool = True
    prompt: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "source_session_id": self.source_session_id,
            "compact_summary": self.compact_summary.to_dict() if self.compact_summary else None,
            "tail_message_count": len(self.tail_messages),
            "current_artifact_refs": list(self.current_artifact_refs),
            "pinned_refs": list(self.pinned_refs),
            "current_deliverable_chars": self.current_deliverable_chars,
            "deliverable_inline_level": self.deliverable_inline_level,
            "voice_pack_skipped": self.voice_pack_skipped,
            "prompt": self.prompt,
        }


def _deliverable_fallback_chain(inline_level: str) -> DeliverableFallbackChain:
    if inline_level == "full":
        return "full"
    if inline_level == "tail":
        return "tail"
    return "none"


@dataclass(frozen=True)
class ResumeDiagnostics:
    connect_or_fork: ResumeMode
    decision_reason: list[str]
    forced_by_flag: bool
    source_session_id: str
    target_session_id: str
    session_age_minutes: float | None = None
    context_usage_ratio: float | None = None
    deliverable_inline_level: str = "none"
    current_deliverable_chars: int = 0
    tail_message_count: int = 0
    voice_pack_skipped: bool = True
    current_artifact_ref_count: int = 0
    pinned_ref_count: int = 0
    deliverable_fallback_chain: DeliverableFallbackChain = "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "connect_or_fork": self.connect_or_fork,
            "decision_reason": list(self.decision_reason),
            "forced_by_flag": self.forced_by_flag,
            "source_session_id": self.source_session_id,
            "target_session_id": self.target_session_id,
            "session_age_minutes": round(self.session_age_minutes, 2)
            if self.session_age_minutes is not None
            else None,
            "context_usage_ratio": round(self.context_usage_ratio, 4)
            if self.context_usage_ratio is not None
            else None,
            "deliverable_inline_level": self.deliverable_inline_level,
            "current_deliverable_chars": self.current_deliverable_chars,
            "tail_message_count": self.tail_message_count,
            "voice_pack_skipped": self.voice_pack_skipped,
            "current_artifact_ref_count": self.current_artifact_ref_count,
            "pinned_ref_count": self.pinned_ref_count,
            "deliverable_fallback_chain": self.deliverable_fallback_chain,
        }


def _resume_diagnostics(
    *,
    decision: ResumeDecision,
    final_state: ResumeFinalState,
) -> ResumeDiagnostics:
    return ResumeDiagnostics(
        connect_or_fork=decision.connect_or_fork,
        decision_reason=list(decision.decision_reason),
        forced_by_flag=decision.forced_by_flag,
        source_session_id=decision.source_session_id,
        target_session_id=decision.target_session_id,
        session_age_minutes=decision.session_age_minutes,
        context_usage_ratio=decision.context_usage_ratio,
        deliverable_inline_level=final_state.deliverable_inline_level,
        current_deliverable_chars=final_state.current_deliverable_chars,
        tail_message_count=len(final_state.tail_messages),
        voice_pack_skipped=final_state.voice_pack_skipped,
        current_artifact_ref_count=len(final_state.current_artifact_refs),
        pinned_ref_count=len(final_state.pinned_refs),
        deliverable_fallback_chain=_deliverable_fallback_chain(
            final_state.deliverable_inline_level
        ),
    )


@dataclass(frozen=True)
class ResumeResult:
    status: Literal["ok", "error"]
    task: TaskEntity | None = None
    decision: ResumeDecision | None = None
    final_state: ResumeFinalState | None = None
    runtime_session: StartedSession | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"status": self.status}
        if self.reason:
            data["reason"] = self.reason
        if self.task is not None:
            data["task"] = self.task.__dict__
        if self.final_state is not None:
            data["final_state"] = self.final_state.to_dict()
        if self.runtime_session is not None:
            data["runtime_session"] = self.runtime_session.to_dict()
            data["runtime_status"] = self.runtime_session.status
            data["runtime_session_id"] = self.runtime_session.session_id
        if self.decision is not None and self.final_state is not None:
            data["resume_diagnostics"] = _resume_diagnostics(
                decision=self.decision,
                final_state=self.final_state,
            ).to_dict()
        elif self.decision is not None:
            data["resume_diagnostics"] = self.decision.to_dict()
        return data


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _message_chars(messages: list[TaskMessage]) -> int:
    return sum(len(message.content or "") for message in messages)


def _tail_after_compact(
    store: TaskMemoryStore,
    *,
    session_id: str,
    task_id: str,
    compact_summary: CompactSummaryRecord | None,
) -> list[TaskMessage]:
    after = compact_summary.covered_message_end_id if compact_summary is not None else None
    return store.task_messages_after(
        session_id=session_id,
        task_id=task_id,
        after_message_id=after,
    )


def _project_tail(messages: list[TaskMessage], *, limit: int = 12) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for message in messages[-limit:]:
        role = "User" if message.role == "user" else "Assistant"
        text = " ".join((message.content or "").strip().split())
        if not text:
            continue
        lines.append(
            f"[Previous turn {message.sequence_no}: {role}] "
            f"{text[:1200]}"
        )
    return "\n".join(lines)


def _artifact_text(artifact: object) -> str:
    return str(getattr(artifact, "raw_content", "") or "")


def _artifact_status(artifact: object) -> str:
    return str(getattr(artifact, "status", "") or "")


def _build_deliverable_block(
    *,
    artifact_refs: list[str],
    artifact_store: ArtifactLookupPort | None,
    max_chars: int,
) -> tuple[str, int, str]:
    if artifact_store is None or not artifact_refs:
        return "", 0, "none"
    blocks: list[str] = []
    total = 0
    for artifact_id in artifact_refs:
        artifact = artifact_store.get_artifact(artifact_id)
        if artifact is None or _artifact_status(artifact) == "archived":
            continue
        raw = _artifact_text(artifact)
        if not raw.strip():
            continue
        total += len(raw)
        body = raw
        inline_level = "full"
        if len(body) > max_chars:
            body = body[-max_chars:]
            inline_level = "tail"
        blocks.append(
            f'<current_deliverable artifact_id="{escape(artifact_id, quote=True)}" '
            f'inline_level="{inline_level}">\n{escape(body, quote=False)}\n</current_deliverable>'
        )
    if not blocks:
        return "", 0, "none"
    level = "tail" if total > max_chars else "full"
    return "\n\n".join(blocks), total, level


def _build_resume_prompt(
    *,
    task: TaskEntity,
    source_session_id: str,
    compact_summary: CompactSummaryRecord | None,
    tail_messages: list[TaskMessage],
    deliverable_block: str,
    pinned_refs: list[str],
    voice_pack_skipped: bool,
) -> str:
    parts = [
        '<task_resume source="agent_os_stage4_battle1">',
        f'<task id="{escape(task.task_id, quote=True)}" name="{escape(task.name, quote=True)}" />',
    ]
    if compact_summary is not None:
        parts.append(
            "<compact_summary>\n"
            f"{escape(compact_summary.summary.model_dump_json(indent=2), quote=False)}\n"
            "</compact_summary>"
        )
    tail = _project_tail(tail_messages)
    if tail:
        parts.append(f"<uncompacted_tail>\n{escape(tail, quote=False)}\n</uncompacted_tail>")
    if deliverable_block:
        parts.append(deliverable_block)
    if pinned_refs:
        refs = "\n".join(f"- {escape(ref, quote=False)}" for ref in pinned_refs)
        parts.append(f"<pinned_refs>\n{refs}\n</pinned_refs>")
    if voice_pack_skipped:
        parts.append('<voice_pack skipped="true" reason="voice_pack_none" />')
    parts.append(f'<source_session id="{escape(source_session_id, quote=True)}" />')
    parts.append("</task_resume>")
    return "\n\n".join(parts)


def _context_usage_ratio(messages: list[TaskMessage], context_char_budget: int) -> float | None:
    if context_char_budget <= 0:
        return None
    return _message_chars(messages) / max(1, context_char_budget)


def _session_age_minutes(session: TaskSession | None, now: datetime) -> float | None:
    if session is None:
        return None
    updated = _parse_iso(session.updated_at)
    if updated is None:
        return None
    return max(0.0, (now - updated).total_seconds() / 60.0)


def _decide_resume_mode(
    *,
    source_session_id: str,
    target_session_id: str,
    session: TaskSession | None,
    messages: list[TaskMessage],
    force_mode: ForceMode,
    now: datetime,
    recent_minutes: int,
    context_char_budget: int,
) -> ResumeDecision:
    age = _session_age_minutes(session, now)
    usage = _context_usage_ratio(messages, context_char_budget)
    if force_mode == "connect":
        return ResumeDecision(
            connect_or_fork="connect",
            decision_reason=["forced_connect"],
            forced_by_flag=True,
            source_session_id=source_session_id,
            target_session_id=source_session_id,
            session_age_minutes=age,
            context_usage_ratio=usage,
        )
    if force_mode == "fork":
        return ResumeDecision(
            connect_or_fork="fork",
            decision_reason=["forced_fork"],
            forced_by_flag=True,
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            session_age_minutes=age,
            context_usage_ratio=usage,
        )

    reasons: list[str] = []
    if session is None:
        reasons.append("source_session_missing")
    elif session.status == "archived":
        reasons.append("source_session_archived")
    if age is None:
        reasons.append("session_age_unknown")
    elif age >= recent_minutes:
        reasons.append("session_not_recent")
    if usage is None:
        reasons.append("context_usage_unknown")
    elif usage >= 0.8:
        reasons.append("context_usage_high")
    if reasons:
        return ResumeDecision(
            connect_or_fork="fork",
            decision_reason=reasons,
            forced_by_flag=False,
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            session_age_minutes=age,
            context_usage_ratio=usage,
        )
    return ResumeDecision(
        connect_or_fork="connect",
        decision_reason=["recent_session_under_budget"],
        forced_by_flag=False,
        source_session_id=source_session_id,
        target_session_id=source_session_id,
        session_age_minutes=age,
        context_usage_ratio=usage,
    )


def resume_task(
    *,
    store: TaskMemoryStore,
    task_id: str,
    from_session_id: str | None = None,
    force_mode: ForceMode = None,
    session_id_factory: Callable[[], str] | None = None,
    artifact_store: ArtifactLookupPort | None = None,
    client_id: str = "resume",
    user_id: str | None = None,
    now: datetime | None = None,
    recent_minutes: int = 30,
    context_char_budget: int = 12000,
    max_deliverable_chars: int = 12000,
    skill_id: str | None = None,
    resumed_session_starter: Callable[[str, ResumeSessionMeta], StartedSession] | None = None,
) -> ResumeResult:
    task = store.get_task_entity(task_id)
    if task is None:
        return ResumeResult(status="error", reason="task_not_found")
    if task.status == "archived":
        return ResumeResult(status="error", task=task, reason="task_archived")

    source_session_id = (from_session_id or task.current_main_session_id).strip()
    if not source_session_id:
        return ResumeResult(status="error", task=task, reason="source_session_missing")

    messages = store.task_messages(session_id=source_session_id, task_id=task.task_id)
    compact_summary = store.get_compact_summary(session_id=source_session_id, task_id=task.task_id)
    tail_messages = _tail_after_compact(
        store,
        session_id=source_session_id,
        task_id=task.task_id,
        compact_summary=compact_summary,
    )
    artifact_refs = (
        list(compact_summary.summary.core.current_artifact_refs) if compact_summary is not None else []
    )
    pinned_refs = list(compact_summary.summary.core.pinned_refs) if compact_summary is not None else []
    deliverable_block, deliverable_chars, inline_level = _build_deliverable_block(
        artifact_refs=artifact_refs,
        artifact_store=artifact_store,
        max_chars=max_deliverable_chars,
    )
    prompt = _build_resume_prompt(
        task=task,
        source_session_id=source_session_id,
        compact_summary=compact_summary,
        tail_messages=tail_messages,
        deliverable_block=deliverable_block,
        pinned_refs=pinned_refs,
        voice_pack_skipped=True,
    )
    target_session_id = (session_id_factory or (lambda: source_session_id))()
    decision = _decide_resume_mode(
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        session=store.get_session(source_session_id),
        messages=messages,
        force_mode=force_mode,
        now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc),
        recent_minutes=recent_minutes,
        context_char_budget=context_char_budget,
    )
    if decision.connect_or_fork == "fork":
        store.upsert_session(
            session_id=decision.target_session_id,
            client_id=client_id,
            user_id=user_id,
            active_task_id=task.task_id,
            parent_session_id=source_session_id,
            branch_role="main",
        )
        task = store.set_current_main_session(
            task_id=task.task_id,
            session_id=decision.target_session_id,
        ) or task
    final_state = ResumeFinalState(
        task_id=task.task_id,
        source_session_id=source_session_id,
        compact_summary=compact_summary,
        tail_messages=tail_messages,
        current_artifact_refs=artifact_refs,
        pinned_refs=pinned_refs,
        current_deliverable_chars=deliverable_chars,
        deliverable_inline_level=inline_level,
        voice_pack_skipped=True,
        prompt=prompt,
    )
    runtime_session = None
    if resumed_session_starter is not None:
        runtime_session = resumed_session_starter(
            final_state.prompt,
            ResumeSessionMeta(
                session_id=decision.target_session_id,
                client_id=client_id,
                user_id=user_id,
                skill_id=skill_id,
                task_id=task.task_id,
                source_session_id=source_session_id,
                branch_role="main" if decision.connect_or_fork == "fork" else None,
            ),
        )
        if runtime_session.status != "ok":
            if decision.connect_or_fork == "fork":
                task = store.set_current_main_session(
                    task_id=task.task_id,
                    session_id=source_session_id,
                ) or task
            return ResumeResult(
                status="error",
                task=task,
                decision=decision,
                final_state=final_state,
                runtime_session=runtime_session,
                reason=runtime_session.reason or "runtime_start_failed",
            )
    return ResumeResult(
        status="ok",
        task=task,
        decision=decision,
        final_state=final_state,
        runtime_session=runtime_session,
    )
