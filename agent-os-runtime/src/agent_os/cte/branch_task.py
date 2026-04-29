from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal
from uuid import uuid4

from agent_os.agent.compact import SkillSchemaProvider, SkillSchemaRegistry
from agent_os.agent.task_memory import TaskEntity, TaskMemoryStore, TaskSession
from agent_os.cte.resume_task import ArtifactLookupPort, ResumeFinalState, resume_task
from agent_os.er.resume_session import ResumeSessionMeta, StartedSession


@dataclass(frozen=True)
class BranchResult:
    status: Literal["ok", "error"]
    task: TaskEntity | None = None
    source_session: TaskSession | None = None
    branch_session: TaskSession | None = None
    final_state: ResumeFinalState | None = None
    runtime_session: StartedSession | None = None
    resume_diagnostics: dict[str, object] | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"status": self.status}
        if self.reason:
            data["reason"] = self.reason
        if self.task is not None:
            data["task"] = self.task.__dict__
        if self.source_session is not None:
            data["source_session"] = self.source_session.__dict__
        if self.branch_session is not None:
            data["branch_session"] = self.branch_session.__dict__
        if self.final_state is not None:
            data["final_state"] = self.final_state.to_dict()
        if self.runtime_session is not None:
            data["runtime_session"] = self.runtime_session.to_dict()
            data["runtime_status"] = self.runtime_session.status
            data["runtime_session_id"] = self.runtime_session.session_id
        if self.resume_diagnostics is not None:
            data["resume_diagnostics"] = dict(self.resume_diagnostics)
        return data


def _new_session_id() -> str:
    return str(uuid4())


def branch_task(
    *,
    store: TaskMemoryStore,
    task_id: str,
    from_session_id: str | None = None,
    session_id_factory: Callable[[], str] | None = None,
    artifact_store: ArtifactLookupPort | None = None,
    client_id: str = "branch",
    user_id: str | None = None,
    context_char_budget: int = 12000,
    max_deliverable_chars: int = 12000,
    skill_id: str | None = None,
    skill_schema_provider: SkillSchemaProvider | None = None,
    skill_schema_registry: SkillSchemaRegistry | None = None,
    resumed_session_starter: Callable[[str, ResumeSessionMeta], StartedSession] | None = None,
) -> BranchResult:
    task = store.get_task_entity(task_id)
    if task is None:
        return BranchResult(status="error", reason="task_not_found")
    if task.status == "archived":
        return BranchResult(status="error", task=task, reason="task_archived")

    source_session_id = (from_session_id or task.current_main_session_id).strip()
    if not source_session_id:
        return BranchResult(status="error", task=task, reason="source_session_missing")
    source_session = store.get_session(source_session_id)
    if source_session is None:
        return BranchResult(status="error", task=task, reason="source_session_missing")

    branch_session_id = (session_id_factory or _new_session_id)()
    if branch_session_id == source_session_id:
        return BranchResult(
            status="error",
            task=task,
            source_session=source_session,
            reason="branch_session_conflicts_with_source",
        )

    # Reuse the resume final-state synthesis without mutating the task main pointer.
    resume_result = resume_task(
        store=store,
        task_id=task.task_id,
        from_session_id=source_session_id,
        force_mode="connect",
        session_id_factory=lambda: branch_session_id,
        artifact_store=artifact_store,
        context_char_budget=context_char_budget,
        max_deliverable_chars=max_deliverable_chars,
        skill_id=skill_id,
        skill_schema_provider=skill_schema_provider,
        skill_schema_registry=skill_schema_registry,
    )
    if resume_result.status != "ok" or resume_result.final_state is None:
        return BranchResult(
            status="error",
            task=task,
            source_session=source_session,
            reason=resume_result.reason or "resume_final_state_failed",
        )

    branch_session = store.upsert_session(
        session_id=branch_session_id,
        client_id=client_id,
        user_id=user_id,
        active_task_id=task.task_id,
        parent_session_id=source_session_id,
        branch_role="branch",
    )
    runtime_session = None
    if resumed_session_starter is not None:
        runtime_session = resumed_session_starter(
            resume_result.final_state.prompt,
            ResumeSessionMeta(
                session_id=branch_session_id,
                client_id=client_id,
                user_id=user_id,
                skill_id=skill_id,
                task_id=task.task_id,
                source_session_id=source_session_id,
                branch_role="branch",
            ),
        )
        if runtime_session.status != "ok":
            return BranchResult(
                status="error",
                task=store.get_task_entity(task.task_id) or task,
                source_session=source_session,
                branch_session=branch_session,
                final_state=resume_result.final_state,
                runtime_session=runtime_session,
                resume_diagnostics=resume_result.to_dict().get("resume_diagnostics"),
                reason=runtime_session.reason or "runtime_start_failed",
            )
    return BranchResult(
        status="ok",
        task=store.get_task_entity(task.task_id) or task,
        source_session=source_session,
        branch_session=branch_session,
        final_state=resume_result.final_state,
        runtime_session=runtime_session,
        resume_diagnostics=resume_result.to_dict().get("resume_diagnostics"),
    )
