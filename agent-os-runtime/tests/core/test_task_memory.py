from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_os.agent.task_memory import (
    TaskMemoryStore,
    TaskSummary,
    TaskSummaryService,
    build_task_index_instruction,
    build_task_summary_instruction,
    new_task_id,
)


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
