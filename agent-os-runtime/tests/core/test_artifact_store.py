from __future__ import annotations

from pathlib import Path

import pytest

from agent_os.agent.compact import CompactSummaryService
from agent_os.agent.task_memory import TaskMemoryStore
from agent_os.knowledge.artifact_store import ArtifactStore, artifact_digest_fallback


def test_artifact_store_creates_and_reads_record(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")

    artifact = store.create_artifact(
        task_id="task_1",
        session_id="s1",
        raw_content="完整交付物正文",
        digest="交付物摘要",
    )

    assert artifact.artifact_id.startswith("artifact_")
    assert artifact.status == "active"
    assert artifact.originating_session_id == "s1"
    assert artifact.digest_status == "built"
    assert artifact.ref_digest == "交付物摘要"
    assert store.get_artifact(artifact.artifact_id) == artifact
    assert store.list_artifacts(task_id="task_1") == [artifact]


def test_artifact_store_archives_and_keeps_task_isolation(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    a1 = store.create_artifact(task_id="task_1", session_id="s1", raw_content="task 1 正文")
    store.create_artifact(task_id="task_2", session_id="s2", raw_content="task 2 正文")

    archived = store.archive_artifact(a1.artifact_id)

    assert archived is not None
    assert archived.status == "archived"
    assert store.list_artifacts(task_id="task_1") == []
    assert store.list_artifacts(task_id="task_1", include_archived=True)[0].artifact_id == a1.artifact_id
    assert [a.task_id for a in store.list_artifacts(task_id="task_2")] == ["task_2"]


def test_artifact_store_pending_digest_uses_fallback(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    raw = " ".join(f"片段{i}" for i in range(80))

    artifact = store.create_artifact(task_id="task_1", session_id="s1", raw_content=raw)

    assert artifact.digest is None
    assert artifact.digest_status == "pending"
    assert artifact.ref_digest == artifact_digest_fallback(raw)
    assert len(artifact.ref_digest) <= 200


def test_artifact_store_rejects_empty_raw_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")

    with pytest.raises(ValueError):
        store.create_artifact(task_id="task_1", session_id="s1", raw_content=" ")


def test_artifact_store_reuses_record_by_stable_key(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")

    first = store.create_artifact(
        task_id="task_1",
        session_id="s1",
        raw_content="第一次工具结果正文",
        stable_key="tool-result-key",
    )
    second = store.create_artifact(
        task_id="task_1",
        session_id="s1",
        raw_content="第二次不应写入",
        stable_key="tool-result-key",
    )

    assert second == first
    assert store.find_artifact_by_stable_key("tool-result-key") == first
    assert [a.artifact_id for a in store.list_artifacts(task_id="task_1")] == [first.artifact_id]


def test_artifact_store_lists_orphans_without_deleting(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    active = store.create_artifact(task_id="task_1", session_id="s1", raw_content="task 1 正文")
    orphan = store.create_artifact(task_id="missing_task", session_id="s2", raw_content="orphan 正文")

    orphans = store.list_orphan_artifacts(existing_task_ids={"task_1"})

    assert [a.artifact_id for a in orphans] == [orphan.artifact_id]
    assert store.get_artifact(active.artifact_id) == active
    assert store.get_artifact(orphan.artifact_id) == orphan


def test_artifact_update_in_place_resets_digest_for_origin_session(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    artifact = store.create_artifact(
        task_id="task_1",
        session_id="s1",
        raw_content="第一版正文",
        digest="第一版摘要",
    )

    result = store.update_artifact_content(
        artifact_id=artifact.artifact_id,
        current_session_id="s1",
        raw_content="同 session 第二版正文",
    )

    assert result is not None
    assert result.mode == "in_place"
    assert result.cow_from is None
    assert result.artifact.artifact_id == artifact.artifact_id
    assert result.artifact.raw_content == "同 session 第二版正文"
    assert result.artifact.digest is None
    assert result.artifact.digest_status == "pending"
    assert result.artifact.originating_session_id == "s1"


def test_artifact_update_cross_session_cow_updates_current_compact_refs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    artifact_store = ArtifactStore(tmp_path / "artifacts.db")
    task_store = TaskMemoryStore(tmp_path / "task.db")
    task = task_store.create_task(name="春季宣发方案", current_main_session_id="s-branch")
    task_store.upsert_session(session_id="s1", client_id="c1", active_task_id=task.task_id)
    task_store.upsert_session(
        session_id="s-branch",
        client_id="c1",
        active_task_id=task.task_id,
        parent_session_id="s1",
        branch_role="branch",
    )
    artifact = artifact_store.create_artifact(
        task_id=task.task_id,
        session_id="s1",
        raw_content="主线原始版本",
        digest="主线摘要",
    )
    task_store.append_message(
        session_id="s-branch",
        task_id=task.task_id,
        role="user",
        content="分支需要改稿",
    )
    CompactSummaryService(task_store).compact(
        session_id="s-branch",
        task_id=task.task_id,
        current_artifact_refs=[artifact.artifact_id],
    )

    result = artifact_store.update_artifact_content(
        artifact_id=artifact.artifact_id,
        current_session_id="s-branch",
        raw_content="分支复制后的版本",
        task_memory_db_path=tmp_path / "task.db",
    )
    old = artifact_store.get_artifact(artifact.artifact_id)
    branch_summary = task_store.get_compact_summary(session_id="s-branch", task_id=task.task_id)

    assert result is not None
    assert result.mode == "cow"
    assert result.cow_from == artifact.artifact_id
    assert result.compact_refs_updated is True
    assert result.artifact.artifact_id != artifact.artifact_id
    assert result.artifact.session_id == "s-branch"
    assert result.artifact.originating_session_id == "s-branch"
    assert result.artifact.digest_status == "pending"
    assert old is not None
    assert old.raw_content == "主线原始版本"
    assert branch_summary is not None
    assert branch_summary.summary.core.current_artifact_refs == [result.artifact.artifact_id]
