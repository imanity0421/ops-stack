from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_os.agent.factory import get_agent
from agent_os.config import Settings
from agent_os.memory.controller import MemoryController

SessionId = str


@dataclass(frozen=True)
class ResumeSessionMeta:
    session_id: SessionId
    client_id: str
    user_id: str | None = None
    skill_id: str | None = None
    task_id: str | None = None
    source_session_id: str | None = None
    branch_role: str | None = None
    thought_mode: str = "fast"

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "client_id": self.client_id,
            "user_id": self.user_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "source_session_id": self.source_session_id,
            "branch_role": self.branch_role,
            "thought_mode": self.thought_mode,
        }


@dataclass(frozen=True)
class StartedSession:
    status: Literal["ok", "error"]
    session_id: SessionId
    output_text: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "status": self.status,
            "session_id": self.session_id,
            "output_text": self.output_text,
        }
        if self.reason:
            data["reason"] = self.reason
        return data


def _default_controller(settings: Settings) -> MemoryController:
    return MemoryController.create_default(
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


def start_resumed_session(
    prompt: str,
    session_meta: ResumeSessionMeta,
    *,
    settings: Settings | None = None,
    controller: MemoryController | None = None,
) -> StartedSession:
    """Spin up an Agno session from a CTE-synthesized resume prompt."""

    s = settings or Settings.from_env()
    ctrl = controller or _default_controller(s)
    try:
        agent = get_agent(
            ctrl,
            client_id=session_meta.client_id,
            user_id=session_meta.user_id,
            thought_mode=session_meta.thought_mode,
            settings=s,
            skill_id=session_meta.skill_id,
            entrypoint="api",
        )
        output = agent.run(
            prompt,
            session_id=session_meta.session_id,
            user_id=session_meta.user_id or session_meta.client_id,
            stream=False,
        )
        content = output.content
        text = content if isinstance(content, str) else str(content)
        return StartedSession(status="ok", session_id=session_meta.session_id, output_text=text)
    except Exception as exc:
        return StartedSession(
            status="error",
            session_id=session_meta.session_id,
            reason=f"{type(exc).__name__}: {exc}",
        )
