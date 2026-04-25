"""MemoryController 与本地后端单元测试（不依赖 Mem0 云端）。"""

import json
from pathlib import Path
from typing import Any

import pytest

from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemorySearchHit
from agent_os.memory.models import MemoryLane, UserFact


@pytest.fixture
def tmp_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "local.json", tmp_path / "hindsight.jsonl"


def test_ingest_attribute_writes_mem0(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    r = ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="c1",
            user_id="u1",
            text="产品主打低价高频",
            fact_type="attribute",
        )
    )
    assert r.dedup_skipped is False
    assert "mem0" in r.written_to

    hits = ctrl.search_profile("低价", client_id="c1", user_id="u1")
    assert len(hits) >= 1
    assert "低价" in hits[0].text


def test_dedup_skips_duplicate(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    fact = UserFact(
        lane=MemoryLane.ATTRIBUTE,
        client_id="c1",
        text="偏好轻松语气",
        fact_type="preference",
    )
    r1 = ctrl.ingest_user_fact(fact)
    r2 = ctrl.ingest_user_fact(fact)
    assert r1.dedup_skipped is False
    assert r2.dedup_skipped is True


def test_dedup_is_scoped_by_user_id(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    text = "以后所有交付物默认不要使用夸张表述"
    r1 = ctrl.ingest_user_fact(
        UserFact(lane=MemoryLane.ATTRIBUTE, client_id="c1", user_id="u1", text=text)
    )
    r2 = ctrl.ingest_user_fact(
        UserFact(lane=MemoryLane.ATTRIBUTE, client_id="c1", user_id="u2", text=text)
    )

    assert r1.dedup_skipped is False
    assert r2.dedup_skipped is False
    assert "mem0" in r1.written_to
    assert "mem0" in r2.written_to


class _FlakyBackend:
    def __init__(self) -> None:
        self.fail = True

    def mem_user_id(self, client_id: str, user_id: str | None) -> str:
        return f"{client_id}::{user_id or ''}"

    def add_messages(
        self,
        *,
        messages: list[dict[str, str]],
        client_id: str,
        user_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        _ = (messages, client_id, user_id, metadata)
        if self.fail:
            self.fail = False
            raise RuntimeError("transient")
        return {"status": "ok"}

    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        limit: int,
    ) -> list[MemorySearchHit]:
        _ = (query, client_id, user_id, limit)
        return []

    def snapshot_client_profile(self, client_id: str, user_id: str | None) -> None:
        _ = (client_id, user_id)


def test_failed_write_does_not_poison_dedup_fingerprint() -> None:
    backend = _FlakyBackend()
    ctrl = MemoryController(
        backend,
        hindsight=None,
        enable_memory_policy=False,
    )
    fact = UserFact(
        lane=MemoryLane.ATTRIBUTE,
        client_id="c1",
        user_id=None,
        text="transient write should retry",
    )

    with pytest.raises(RuntimeError):
        ctrl.ingest_user_fact(fact)
    r = ctrl.ingest_user_fact(fact)

    assert r.dedup_skipped is False
    assert "mem0" in r.written_to


def test_task_feedback_writes_stub(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    r = ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            task_id="t1",
            deliverable_type="策划案",
            text="用户认为方案太硬",
            fact_type="feedback",
        )
    )
    assert "hindsight" in r.written_to
    assert hind.exists()
    assert "太硬" in hind.read_text(encoding="utf-8")


def test_disable_hindsight_disallows_task_feedback(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
        enable_hindsight=False,
    )
    with pytest.raises(RuntimeError):
        ctrl.ingest_user_fact(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text="用户明确反馈不满意，需要下次改进",
                fact_type="feedback",
            )
        )


def test_snapshot_counter(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
        snapshot_every_n_turns=2,
    )
    for _ in range(2):
        ctrl.bump_turn_and_maybe_snapshot("c1", "u1")
    # 第二次应触发 snapshot 钩子（本地后端仅打日志，不抛错）
    ctrl.bump_turn_and_maybe_snapshot("c1", "u1")


def test_snapshot_counter_zero_disables_snapshot_without_crashing(
    tmp_paths: tuple[Path, Path],
) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
        snapshot_every_n_turns=0,
    )
    ctrl.bump_turn_and_maybe_snapshot("c1", "u1")
    ctrl.bump_turn_and_maybe_snapshot("c1", "u1")


def test_local_memory_bad_json_starts_with_empty_view(tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hind = tmp_path / "hindsight.jsonl"
    local.write_text("{not-json", encoding="utf-8")

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )

    assert ctrl.search_profile("", client_id="c1", user_id=None) == []


def test_local_memory_accepts_utf8_bom(tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hind = tmp_path / "hindsight.jsonl"
    local.write_text(
        '\ufeff{"users": {"c1": {"memories": [{"text": "valid bom", "metadata": {}}]}}}',
        encoding="utf-8",
    )

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )

    assert ctrl.search_profile("bom", client_id="c1", user_id=None)[0].text == "valid bom"


def test_local_memory_non_object_root_starts_with_empty_view(tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hind = tmp_path / "hindsight.jsonl"
    local.write_text("[]", encoding="utf-8")

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )

    assert ctrl.search_profile("", client_id="c1", user_id=None) == []


def test_local_memory_malformed_user_bucket_does_not_crash(tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hind = tmp_path / "hindsight.jsonl"
    local.write_text(
        json.dumps(
            {
                "users": {
                    "c1": [],
                    "c2": {"memories": "bad"},
                    "c3": {"memories": [{"text": 123}, "bad", {"text": "valid"}]},
                }
            }
        ),
        encoding="utf-8",
    )

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )

    assert ctrl.search_profile("", client_id="c1", user_id=None) == []
    assert ctrl.search_profile("", client_id="c2", user_id=None) == []
    hits = ctrl.search_profile("", client_id="c3", user_id=None)
    assert [h.text for h in hits] == ["valid"]


def test_memory_policy_rejects_uncertain_temporary_fact(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    r = ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="c1",
            text="哈哈我开玩笑的，暂时随便说说",
            fact_type="attribute",
        )
    )
    assert r.policy_rejected is True
    assert r.dedup_reason is not None
    assert r.dedup_reason.startswith("policy_rejected:")
    assert not local.exists()


def test_memory_policy_allows_stable_preference_and_records_metadata(
    tmp_paths: tuple[Path, Path],
) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    r = ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="c1",
            user_id="u1",
            text="以后所有交付物默认不要使用夸张表述",
            fact_type="preference",
            source="manual",
            confidence=0.9,
        )
    )
    assert r.policy_rejected is False
    assert "mem0" in r.written_to

    data = json.loads(local.read_text(encoding="utf-8"))
    mem = data["users"]["c1::u1"]["memories"][0]
    meta = mem["metadata"]
    assert meta["recorded_at"]
    assert meta["memory_source"] == "manual"
    assert meta["confidence"] == 0.9

    hits = ctrl.search_profile("交付物", client_id="c1", user_id="u1")
    assert hits[0].metadata["recorded_at"]


def test_hindsight_records_and_renders_recorded_at(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="用户认为方案方向错了，下次先确认关键约束",
            fact_type="feedback",
            source="agent_tool",
        )
    )
    row = json.loads(hind.read_text(encoding="utf-8").splitlines()[0])
    assert row["recorded_at"]
    assert row["source"] == "agent_tool"

    rendered = ctrl.search_hindsight("关键约束", client_id="c1")
    assert rendered
    assert rendered[0].startswith("[记录于 ")
    assert "下次先确认关键约束" in rendered[0]


def test_hindsight_skips_malformed_rows(tmp_paths: tuple[Path, Path]) -> None:
    _local, hind = tmp_paths
    hind.write_text(
        '["not", "object"]\n{"client_id": "c1", "type": "lesson", "text": 123}\n'
        '{"client_id": "c1", "type": "lesson", "text": "valid lesson"}\n',
        encoding="utf-8",
    )
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=_local,
        hindsight_path=hind,
    )

    assert ctrl.search_hindsight("", client_id="c1") == [
        "[记录于 记录时间未知 | 来源 unknown] valid lesson"
    ]


def test_hindsight_directory_path_returns_empty(tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hind = tmp_path / "hindsight-dir"
    hind.mkdir()
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )

    assert ctrl.search_hindsight("", client_id="c1") == []


def test_hindsight_accepts_utf8_bom(tmp_paths: tuple[Path, Path]) -> None:
    local, hind = tmp_paths
    hind.write_text(
        '\ufeff{"client_id": "c1", "type": "lesson", "text": "valid bom"}\n',
        encoding="utf-8",
    )
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )

    assert ctrl.search_hindsight("bom", client_id="c1") == [
        "[记录于 记录时间未知 | 来源 unknown] valid bom"
    ]
