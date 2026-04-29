from __future__ import annotations

from pathlib import Path

import pytest

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
