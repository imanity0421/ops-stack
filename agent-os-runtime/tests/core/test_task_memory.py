from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from agent_os.agent.compact import (
    CompactSummaryService,
    SkillSchemaProvider,
    compose_compact_summary_schema,
    resolve_skill_schema_fragment,
)
from agent_os.agent.task_memory import (
    TaskMemoryStore,
    TaskSummary,
    TaskSummaryService,
    build_task_index_instruction,
    build_task_summary_instruction,
    new_task_id,
)
from agent_os.cte.branch_task import branch_task
from agent_os.cte.resume_task import resume_task
from agent_os.er.resume_session import ResumeSessionMeta, StartedSession
from agent_os.knowledge.artifact_store import ArtifactStore
from agent_os.sr.schema_registry import SkillSchemaProviderRegistry


def test_new_task_id_is_human_sortable() -> None:
    tid = new_task_id()
    assert tid.startswith("task_")
    assert len(tid.split("_")) == 3


def test_get_or_create_active_task_reuses_same_session_task(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    t1 = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="帮我完成任务方案",
    )
    t2 = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="继续改刚才那个",
    )
    assert t1.task_id == t2.task_id
    assert t1.task_title == "帮我完成任务方案"


def test_task_entity_crud_uses_five_field_schema(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")

    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")

    assert task.task_id.startswith("task_")
    assert task.name == "春季宣发方案"
    assert task.status == "active"
    assert task.current_main_session_id == "s1"
    assert store.get_task_entity(task.task_id) == task
    assert store.list_task_entities() == [task]

    archived = store.archive_task_entity(task.task_id)
    assert archived is not None
    assert archived.status == "archived"
    assert store.list_task_entities() == []
    assert store.list_task_entities(include_archived=True)[0].status == "archived"

    restored = store.unarchive_task_entity(task.task_id)
    assert restored is not None
    assert restored.status == "active"


def test_task_session_api_and_current_main_session_update(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")

    session = store.upsert_session(
        session_id="s1",
        client_id="c1",
        user_id="u1",
        active_task_id=task.task_id,
    )
    updated = store.set_current_main_session(task_id=task.task_id, session_id="s2")

    assert session.active_task_id == task.task_id
    assert session.status == "active"
    assert session.parent_session_id is None
    assert session.branch_role is None
    assert updated is not None
    assert updated.current_main_session_id == "s2"

    branch = store.upsert_session(
        session_id="s3",
        client_id="c1",
        active_task_id=task.task_id,
        parent_session_id="s1",
        branch_role="branch",
    )
    main = store.set_session_branch_metadata(
        session_id="s1",
        parent_session_id=None,
        branch_role="main",
    )

    assert branch.parent_session_id == "s1"
    assert branch.branch_role == "branch"
    assert main is not None
    assert main.branch_role == "main"


def test_get_or_create_active_task_backfills_task_entity(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")

    segment = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="帮我完成任务方案",
    )

    entity = store.get_task_entity(segment.task_id)
    assert entity is not None
    assert entity.name == "帮我完成任务方案"
    assert entity.current_main_session_id == "s1"


def test_task_memory_sqlite_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    db = tmp_path / "task.db"
    store = TaskMemoryStore(db)
    store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="任务",
    )

    with store._connect() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert busy_timeout >= 10000
    assert journal_mode == "wal"


def test_task_summary_upsert_overwrites(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id="u1",
        skill_id="sample_skill",
        seed_message="制定一个交付方案",
    )
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="第一条")
    store.upsert_summary(
        TaskSummary(
            session_id="s1",
            task_id=task.task_id,
            summary_text="- 当前任务目标：第一版",
            summary_version=1,
            covered_message_count=1,
            updated_at="2026-04-25T00:00:00+00:00",
        )
    )
    store.upsert_summary(
        TaskSummary(
            session_id="s1",
            task_id=task.task_id,
            summary_text="- 当前任务目标：第二版",
            summary_version=2,
            covered_message_count=2,
            updated_at="2026-04-25T00:01:00+00:00",
        )
    )
    summary = store.get_summary(session_id="s1", task_id=task.task_id)
    assert summary is not None
    assert summary.summary_version == 2
    assert "第二版" in summary.summary_text


def test_task_prompt_helpers(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="任务产出优化",
    )
    summary = TaskSummary(
        session_id="s1",
        task_id=task.task_id,
        summary_text="- 当前任务目标：优化当前交付物",
        summary_version=1,
        covered_message_count=3,
        updated_at="2026-04-25T00:00:00+00:00",
    )
    summary_inst = build_task_summary_instruction(summary)
    index_inst = build_task_index_instruction(store.task_index(session_id="s1"))
    assert summary_inst is not None
    assert "【当前任务前情提要】" in summary_inst
    assert "不得自动写入 Mem0" in summary_inst
    assert index_inst is not None
    assert "本 session 任务目录" in index_inst


def test_task_summary_instruction_declares_not_long_term_memory() -> None:
    summary = TaskSummary(
        session_id="s1",
        task_id="t1",
        summary_text="- 当前任务目标：优化当前交付物",
        summary_version=1,
        covered_message_count=3,
        updated_at="2026-04-25T00:00:00+00:00",
    )

    instruction = build_task_summary_instruction(summary)

    assert instruction is not None
    assert "仅用于当前 session/task 连贯性" in instruction
    assert "不得自动写入 Mem0" in instruction
    assert "不得自动写入 Hindsight" in instruction
    assert "不得自动写入 Asset" in instruction
    assert "不得自动写入 Graphiti" in instruction


def test_task_summary_service_rolls_up_messages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="制定一个交付方案",
    )
    for i in range(4):
        store.append_message(
            session_id="s1",
            task_id=task.task_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"第 {i} 条任务消息，需要继续推进方案",
        )
    service = TaskSummaryService(
        store,
        max_chars=500,
        min_messages=4,
        every_n_messages=2,
    )

    summary = service.maybe_update(session_id="s1", task_id=task.task_id)

    assert summary is not None
    assert summary.covered_message_count == 4
    assert summary.summary_version == 1
    assert "当前任务目标" in summary.summary_text
    loaded = store.get_summary(session_id="s1", task_id=task.task_id)
    assert loaded is not None
    assert loaded.covered_message_end_id == summary.covered_message_end_id


def test_compact_summary_service_persists_structured_summary(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="帮我写春季宣发方案",
    )
    store.append_message(
        session_id="s1",
        task_id=task.task_id,
        role="user",
        content="必须突出新品上市，不要使用夸张承诺。",
    )
    store.append_message(
        session_id="s1",
        task_id=task.task_id,
        role="assistant",
        content="已完成第一版方案结构。",
    )

    record = CompactSummaryService(store).compact(
        session_id="s1",
        task_id=task.task_id,
        current_artifact_refs=["artifact_1"],
        pinned_refs=["asset_1"],
    )

    assert record is not None
    assert record.summary.schema_version == "v2"
    assert record.summary.core.current_artifact_refs == ["artifact_1"]
    assert record.summary.core.pinned_refs == ["asset_1"]
    assert record.summary.core.goal
    assert record.summary.core.last_user_instruction.startswith("必须突出新品上市")
    loaded = store.get_compact_summary(session_id="s1", task_id=task.task_id)
    assert loaded is not None
    assert loaded.summary_version == 1
    assert loaded.summary.core.current_artifact_refs == ["artifact_1"]


def test_resume_task_connects_recent_session_under_budget(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="继续这个方案")

    result = resume_task(store=store, task_id=task.task_id, session_id_factory=lambda: "s2")

    assert result.status == "ok"
    assert result.decision is not None
    assert result.decision.connect_or_fork == "connect"
    assert result.decision.target_session_id == "s1"
    assert result.final_state is not None
    assert "voice_pack skipped=\"true\"" in result.final_state.prompt
    assert store.get_task_entity(task.task_id).current_main_session_id == "s1"


def test_resume_task_force_fork_updates_current_main_session_and_projects_tail(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="必须突出新品")
    store.append_message(session_id="s1", task_id=task.task_id, role="assistant", content="已完成第一版")
    record = CompactSummaryService(store).compact(
        session_id="s1",
        task_id=task.task_id,
        current_artifact_refs=["artifact_1"],
    )
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="继续优化标题")

    result = resume_task(
        store=store,
        task_id=task.task_id,
        force_mode="fork",
        session_id_factory=lambda: "s2",
    )

    assert record is not None
    assert result.status == "ok"
    assert result.decision is not None
    assert result.decision.connect_or_fork == "fork"
    assert result.decision.forced_by_flag is True
    assert result.final_state is not None
    assert result.final_state.compact_summary is not None
    assert result.final_state.current_artifact_refs == ["artifact_1"]
    assert "[Previous turn" in result.final_state.prompt
    assert "继续优化标题" in result.final_state.prompt
    assert store.get_task_entity(task.task_id).current_main_session_id == "s2"
    fork_session = store.get_session("s2")
    assert fork_session is not None
    assert fork_session.parent_session_id == "s1"
    assert fork_session.branch_role == "main"


def test_resume_task_starts_runtime_with_final_state_prompt(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="继续推进")
    calls: list[tuple[str, ResumeSessionMeta]] = []

    def fake_start(prompt: str, session_meta: ResumeSessionMeta) -> StartedSession:
        calls.append((prompt, session_meta))
        return StartedSession(status="ok", session_id=session_meta.session_id, output_text="started")

    result = resume_task(
        store=store,
        task_id=task.task_id,
        force_mode="fork",
        session_id_factory=lambda: "s2",
        client_id="c-runtime",
        user_id="u1",
        skill_id="mock_skill",
        resumed_session_starter=fake_start,
    )

    assert result.status == "ok"
    assert result.runtime_session is not None
    assert result.runtime_session.session_id == "s2"
    assert calls
    prompt, meta = calls[0]
    assert "<task_resume" in prompt
    assert meta.session_id == "s2"
    assert meta.client_id == "c-runtime"
    assert meta.user_id == "u1"
    assert meta.skill_id == "mock_skill"
    assert meta.task_id == task.task_id
    assert meta.source_session_id == "s1"
    assert meta.branch_role == "main"
    diagnostics = result.to_dict()["resume_diagnostics"]
    assert diagnostics["active_skill_id"] == "mock_skill"
    assert diagnostics["skill_fragment_skipped"] is True
    assert diagnostics["skill_fragment_skip_reason"] == "provider_missing"


def test_gc6_resume_stale_session_forks_with_compact_tail_and_refs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = TaskMemoryStore(tmp_path / "task.db")
    artifacts = ArtifactStore(tmp_path / "artifacts.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    session = store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="必须突出新品")
    artifact = artifacts.create_artifact(
        task_id=task.task_id,
        session_id="s1",
        raw_content="主线当前交付物",
        digest="主线摘要",
    )
    compact = CompactSummaryService(store).compact(
        session_id="s1",
        task_id=task.task_id,
        current_artifact_refs=[artifact.artifact_id],
    )
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="继续优化标题")
    now = datetime.fromisoformat(session.updated_at).astimezone(timezone.utc) + timedelta(minutes=31)

    result = resume_task(
        store=store,
        task_id=task.task_id,
        session_id_factory=lambda: "s2",
        artifact_store=artifacts,
        now=now,
        recent_minutes=30,
    )
    payload = result.to_dict()
    diagnostics = payload["resume_diagnostics"]

    assert compact is not None
    assert result.status == "ok"
    assert diagnostics["connect_or_fork"] == "fork"
    assert diagnostics["decision_reason"] == ["session_not_recent"]
    assert diagnostics["tail_message_count"] == 1
    assert diagnostics["current_artifact_ref_count"] == 1
    assert diagnostics["voice_pack_skipped"] is True
    assert payload["final_state"]["compact_summary"] is not None
    assert "继续优化标题" in payload["final_state"]["prompt"]


def test_resume_task_diagnostics_include_final_state_and_fallback_chain(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = TaskMemoryStore(tmp_path / "task.db")
    artifacts = ArtifactStore(tmp_path / "artifacts.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="继续优化长方案")
    artifact = artifacts.create_artifact(
        task_id=task.task_id,
        session_id="s1",
        raw_content="当前交付物正文" * 20,
        digest="交付物摘要",
    )
    CompactSummaryService(store).compact(
        session_id="s1",
        task_id=task.task_id,
        current_artifact_refs=[artifact.artifact_id],
        pinned_refs=["asset_1"],
    )

    result = resume_task(
        store=store,
        task_id=task.task_id,
        force_mode="fork",
        session_id_factory=lambda: "s2",
        artifact_store=artifacts,
        max_deliverable_chars=10,
    )
    diagnostics = result.to_dict()["resume_diagnostics"]

    assert result.status == "ok"
    assert diagnostics["connect_or_fork"] == "fork"
    assert diagnostics["deliverable_inline_level"] == "tail"
    assert diagnostics["deliverable_fallback_chain"] == "tail"
    assert diagnostics["current_deliverable_chars"] > 10
    assert diagnostics["tail_message_count"] == 0
    assert diagnostics["voice_pack_skipped"] is True
    assert diagnostics["current_artifact_ref_count"] == 1
    assert diagnostics["pinned_ref_count"] == 1


def test_branch_task_creates_branch_session_without_polluting_main_summary(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="主线强调新品")
    main_record = CompactSummaryService(store).compact(
        session_id="s1",
        task_id=task.task_id,
        current_artifact_refs=["artifact_main"],
    )

    result = branch_task(store=store, task_id=task.task_id, session_id_factory=lambda: "s-branch")
    store.append_message(
        session_id="s-branch",
        task_id=task.task_id,
        role="user",
        content="分支改成严谨版",
    )
    branch_record = CompactSummaryService(store).compact(
        session_id="s-branch",
        task_id=task.task_id,
        current_artifact_refs=["artifact_branch"],
    )
    main_summary = store.get_compact_summary(session_id="s1", task_id=task.task_id)
    branch_summary = store.get_compact_summary(session_id="s-branch", task_id=task.task_id)

    assert main_record is not None
    assert branch_record is not None
    assert result.status == "ok"
    assert result.branch_session is not None
    assert result.branch_session.parent_session_id == "s1"
    assert result.branch_session.branch_role == "branch"
    assert result.final_state is not None
    assert result.final_state.current_artifact_refs == ["artifact_main"]
    assert store.get_task_entity(task.task_id).current_main_session_id == "s1"
    assert main_summary is not None
    assert main_summary.summary.core.current_artifact_refs == ["artifact_main"]
    assert branch_summary is not None
    assert branch_summary.summary.core.current_artifact_refs == ["artifact_branch"]


def test_branch_task_starts_runtime_after_branch_session_creation(tmp_path: Path) -> None:
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="春季宣发方案", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="做分支版本")
    calls: list[tuple[str, ResumeSessionMeta]] = []

    def fake_start(prompt: str, session_meta: ResumeSessionMeta) -> StartedSession:
        calls.append((prompt, session_meta))
        return StartedSession(status="ok", session_id=session_meta.session_id, output_text="branch")

    result = branch_task(
        store=store,
        task_id=task.task_id,
        session_id_factory=lambda: "s-branch",
        client_id="branch-client",
        user_id="u1",
        skill_id="mock_skill",
        resumed_session_starter=fake_start,
    )

    assert result.status == "ok"
    assert result.branch_session is not None
    assert result.runtime_session is not None
    assert result.runtime_session.session_id == "s-branch"
    assert calls
    prompt, meta = calls[0]
    assert "<task_resume" in prompt
    assert meta.session_id == "s-branch"
    assert meta.client_id == "branch-client"
    assert meta.user_id == "u1"
    assert meta.skill_id == "mock_skill"
    assert meta.task_id == task.task_id
    assert meta.source_session_id == "s1"
    assert meta.branch_role == "branch"
    diagnostics = result.to_dict()["resume_diagnostics"]
    assert diagnostics["active_skill_id"] == "mock_skill"
    assert diagnostics["skill_fragment_skipped"] is True
    assert diagnostics["skill_fragment_skip_reason"] == "provider_missing"


def test_stage5_battle4_mock_skills_reach_resume_and_branch_runtime(tmp_path: Path) -> None:
    class MockSkillAState(BaseModel):
        a1: str
        a2: int = 0
        a3: list[str] = []

    class MockSkillBState(BaseModel):
        b1: bool
        b2: dict[str, str] = {}

    class MockSkillAProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillAState

    class MockSkillBProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillBState

    registry = SkillSchemaProviderRegistry()
    registry.register("mock_skill_a", MockSkillAProvider())
    registry.register("mock_skill_b", MockSkillBProvider())
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="mock sr equality", current_main_session_id="s-a")
    store.upsert_session(session_id="s-a", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s-a", task_id=task.task_id, role="user", content="start")
    calls: list[tuple[str, ResumeSessionMeta]] = []

    def fake_start(prompt: str, session_meta: ResumeSessionMeta) -> StartedSession:
        calls.append((prompt, session_meta))
        return StartedSession(status="ok", session_id=session_meta.session_id, output_text="ok")

    resume_result = resume_task(
        store=store,
        task_id=task.task_id,
        force_mode="fork",
        session_id_factory=lambda: "s-a-resumed",
        skill_id="mock_skill_a",
        skill_schema_registry=registry,
        resumed_session_starter=fake_start,
    )
    branch_result = branch_task(
        store=store,
        task_id=task.task_id,
        from_session_id="s-a-resumed",
        session_id_factory=lambda: "s-b-branch",
        skill_id="mock_skill_b",
        skill_schema_registry=registry,
        resumed_session_starter=fake_start,
    )

    assert resume_result.status == "ok"
    resume_diag = resume_result.to_dict()["resume_diagnostics"]
    assert resume_diag["active_skill_id"] == "mock_skill_a"
    assert resume_diag["skill_fragment_skipped"] is False
    assert branch_result.status == "ok"
    branch_diag = branch_result.to_dict()["resume_diagnostics"]
    assert branch_diag["active_skill_id"] == "mock_skill_b"
    assert branch_diag["skill_fragment_skipped"] is False
    assert [meta.skill_id for _prompt, meta in calls] == ["mock_skill_a", "mock_skill_b"]
    assert all("<task_resume" in prompt for prompt, _meta in calls)


def test_stage5_battle4_cross_skill_artifact_ref_shared_without_schema_coupling(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class MockSkillAState(BaseModel):
        a1: str
        a2: int = 0
        a3: list[str] = []

    class MockSkillBState(BaseModel):
        b1: bool
        b2: dict[str, str] = {}

    class MockSkillAProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillAState

    class MockSkillBProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillBState

    registry = SkillSchemaProviderRegistry()
    registry.register("mock_skill_a", MockSkillAProvider())
    registry.register("mock_skill_b", MockSkillBProvider())
    store = TaskMemoryStore(tmp_path / "task.db")
    artifacts = ArtifactStore(tmp_path / "artifacts.db")
    task_a = store.create_task(name="mock skill a output", current_main_session_id="s-a")
    store.upsert_session(session_id="s-a", client_id="c1", active_task_id=task_a.task_id)
    artifact = artifacts.create_artifact(
        task_id=task_a.task_id,
        session_id="s-a",
        raw_content="out_a.md\nshared artifact body for downstream mock skill",
        digest="shared artifact digest",
        stable_key="out_a.md",
    )
    CompactSummaryService(
        store,
        skill_schema_registry=registry,
        active_skill_id="mock_skill_a",
    ).compact(
        session_id="s-a",
        task_id=task_a.task_id,
        current_artifact_refs=[artifact.artifact_id],
    )

    task_b = store.create_task(name="mock skill b consumes artifact", current_main_session_id="s-b")
    store.upsert_session(session_id="s-b", client_id="c1", active_task_id=task_b.task_id)
    store.append_message(session_id="s-b", task_id=task_b.task_id, role="user", content="consume ref")
    CompactSummaryService(
        store,
        skill_schema_registry=registry,
        active_skill_id="mock_skill_b",
    ).compact(
        session_id="s-b",
        task_id=task_b.task_id,
        current_artifact_refs=[artifact.artifact_id],
    )
    calls: list[tuple[str, ResumeSessionMeta]] = []

    def fake_start(prompt: str, session_meta: ResumeSessionMeta) -> StartedSession:
        calls.append((prompt, session_meta))
        return StartedSession(status="ok", session_id=session_meta.session_id, output_text="ok")

    result = resume_task(
        store=store,
        task_id=task_b.task_id,
        force_mode="fork",
        session_id_factory=lambda: "s-b-resumed",
        artifact_store=artifacts,
        skill_id="mock_skill_b",
        skill_schema_registry=registry,
        resumed_session_starter=fake_start,
    )
    payload = result.to_dict()
    diagnostics = payload["resume_diagnostics"]

    assert task_a.task_id != task_b.task_id
    assert artifact.task_id == task_a.task_id
    assert result.status == "ok"
    assert diagnostics["active_skill_id"] == "mock_skill_b"
    assert diagnostics["skill_fragment_skipped"] is False
    assert diagnostics["current_artifact_ref_count"] == 1
    assert diagnostics["deliverable_inline_level"] == "full"
    assert calls
    prompt = calls[0][0]
    assert artifact.artifact_id in prompt
    assert "shared artifact body for downstream mock skill" in prompt
    assert "a1" not in prompt
    assert "b1" not in prompt


def test_skill_schema_provider_protocol_shape() -> None:
    class SkillState(BaseModel):
        field: str = ""

    class Provider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return SkillState

    provider: SkillSchemaProvider = Provider()
    assert provider.get_compact_schema_fragment() is SkillState


def test_compose_compact_summary_schema_accepts_mock_skill_fragments() -> None:
    class MockSkillAState(BaseModel):
        a1: str
        a2: int = 0
        a3: list[str] = []

    class MockSkillBState(BaseModel):
        b1: bool
        b2: dict[str, str] = {}

    schema_a = compose_compact_summary_schema(MockSkillAState)
    schema_b = compose_compact_summary_schema(MockSkillBState)

    summary_a = schema_a.model_validate(
        {
            "schema_version": "v2",
            "core": {"goal": "mock-a"},
            "skill_state": {"a1": "alpha", "a2": 2, "a3": ["x"]},
        }
    )
    summary_b = schema_b.model_validate(
        {
            "schema_version": "v2",
            "core": {"goal": "mock-b"},
            "skill_state": {"b1": True, "b2": {"k": "v"}},
        }
    )

    assert summary_a.skill_state.a1 == "alpha"
    assert summary_b.skill_state.b1 is True
    schema_a_text = str(schema_a.model_json_schema())
    schema_b_text = str(schema_b.model_json_schema())
    assert "a1" in schema_a_text and "a2" in schema_a_text and "a3" in schema_a_text
    assert "b1" in schema_b_text and "b2" in schema_b_text
    assert "brand" not in schema_a_text.lower()
    assert "voice" not in schema_a_text.lower()
    assert "audience" not in schema_b_text.lower()
    assert "kpi" not in schema_b_text.lower()


def test_skill_fragment_resolution_reports_core_only_fallback_reasons() -> None:
    class MissingFragmentProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel] | None:
            return None

    no_active = resolve_skill_schema_fragment()
    provider_missing = resolve_skill_schema_fragment(active_skill_id="mock_skill")
    fragment_missing = resolve_skill_schema_fragment(
        active_skill_id="mock_skill",
        skill_schema_provider=MissingFragmentProvider(),
    )

    assert no_active.skill_fragment_skipped is True
    assert no_active.skill_fragment_skip_reason == "no_active_skill_id"
    assert provider_missing.skill_fragment_skipped is True
    assert provider_missing.skill_fragment_skip_reason == "provider_missing"
    assert fragment_missing.skill_fragment_skipped is True
    assert fragment_missing.skill_fragment_skip_reason == "fragment_missing"
    assert compose_compact_summary_schema(no_active.skill_state_schema).__name__ == "CompactSummary"


def test_skill_schema_provider_registry_supports_heterogeneous_mock_skills() -> None:
    class MockSkillAState(BaseModel):
        a1: str
        a2: int = 0
        a3: list[str] = []

    class MockSkillBState(BaseModel):
        b1: bool
        b2: dict[str, str] = {}

    class MockSkillAProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillAState

    class MockSkillBProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillBState

    registry = SkillSchemaProviderRegistry()
    registry.register("mock_skill_a", MockSkillAProvider())
    registry.register("mock_skill_b", MockSkillBProvider())

    assert registry.get_schema_fragment("mock_skill_a") is MockSkillAState
    assert registry.get_schema_fragment("mock_skill_b") is MockSkillBState
    assert registry.get_schema_fragment("missing") is None


def test_skill_schema_provider_registry_allows_missing_fragment_provider() -> None:
    class MissingFragmentProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel] | None:
            return None

    registry = SkillSchemaProviderRegistry()
    registry.register("mock_skill_missing", MissingFragmentProvider())

    assert registry.get_schema_fragment("mock_skill_missing") is None


def test_compact_summary_service_uses_registry_fragment_without_persisting_model_instance(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class MockSkillAState(BaseModel):
        a1: str = ""
        a2: int = 0
        a3: list[str] = []

    class MockSkillAProvider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return MockSkillAState

    registry = SkillSchemaProviderRegistry()
    registry.register("mock_skill_a", MockSkillAProvider())
    store = TaskMemoryStore(tmp_path / "task.db")
    task = store.create_task(name="mock task", current_main_session_id="s1")
    store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    store.append_message(session_id="s1", task_id=task.task_id, role="user", content="mock request")

    record = CompactSummaryService(
        store,
        skill_schema_registry=registry,
        active_skill_id="mock_skill_a",
    ).compact(session_id="s1", task_id=task.task_id)

    assert record is not None
    assert record.summary.schema_version == "v2"
    assert record.summary.skill_state is None
    loaded = store.get_compact_summary(session_id="s1", task_id=task.task_id)
    assert loaded is not None
    assert loaded.summary.skill_state is None


def test_task_memory_bad_invoked_skills_json_falls_back(tmp_path: Path) -> None:
    db = tmp_path / "task.db"
    store = TaskMemoryStore(db)
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="任务产出优化",
    )
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE task_segments SET invoked_skills_json = ? WHERE task_id = ?",
            ("{not-json", task.task_id),
        )

    loaded = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="继续",
    )
    assert loaded.invoked_skills == ["default_agent"]
    assert store.task_index(session_id="s1")[0].invoked_skills == ["default_agent"]
