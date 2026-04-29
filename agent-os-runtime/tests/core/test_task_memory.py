from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel

from agent_os.agent.compact import CompactSummaryService, SkillSchemaProvider
from agent_os.agent.task_memory import (
    TaskMemoryStore,
    TaskSummary,
    TaskSummaryService,
    build_task_index_instruction,
    build_task_summary_instruction,
    new_task_id,
)
from agent_os.cte.resume_task import resume_task


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
    assert updated is not None
    assert updated.current_main_session_id == "s2"


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
    assert record.summary.schema_version == "v1"
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


def test_skill_schema_provider_protocol_shape() -> None:
    class SkillState(BaseModel):
        field: str = ""

    class Provider:
        def get_compact_schema_fragment(self) -> type[BaseModel]:
            return SkillState

    provider: SkillSchemaProvider = Provider()
    assert provider.get_compact_schema_fragment() is SkillState


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
