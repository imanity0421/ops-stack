from pathlib import Path

from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.models import MemoryLane, UserFact


def test_hindsight_store_search(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="用户不喜欢太硬的语气",
            fact_type="feedback",
        )
    )
    store.append_lesson(client_id="c1", text="低价品类慎用恐吓营销", source="async_review")
    out = store.search_lessons("低价 营销", "c1", limit=5)
    assert len(out) >= 1


def test_controller_search_hindsight(tmp_path: Path) -> None:
    from agent_os.memory.controller import MemoryController

    local = tmp_path / "l.json"
    hind = tmp_path / "h.jsonl"
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
            text="复盘要点：先共情再推品",
            fact_type="feedback",
        )
    )
    lines = ctrl.search_hindsight("共情", "c1")
    assert any("共情" in x for x in lines)
