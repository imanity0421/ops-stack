"""MemoryController 与本地后端单元测试（不依赖 Mem0 云端）。"""

from pathlib import Path

import pytest

from ops_agent.memory.controller import MemoryController
from ops_agent.memory.models import MemoryLane, UserFact


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
                text="这次不满意",
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
