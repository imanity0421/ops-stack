"""Hindsight 同类合并、频次权重与 supersedes 链。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.models import MemoryLane, UserFact


def test_search_lessons_merges_identical_text_and_shows_freq(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for _ in range(3):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text="同类脚本开头不要铺垫，要先抛冲突。",
                fact_type="feedback",
            )
        )
    out = store.search_lessons("脚本 冲突", "c1", limit=5)
    assert len(out) == 1
    assert "同类×3" in out[0]
    assert "总权重×3" in out[0]


def test_search_lessons_supersedes_hides_obsolete_event(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    p.write_text("", encoding="utf-8")
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="旧版教训：先讲背景再进入正题。",
            fact_type="feedback",
        )
    )
    data = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    old_id = data["event_id"]

    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="新版教训：开头必须先抛冲突，再补背景。",
            fact_type="feedback",
            supersedes_event_id=old_id,
        )
    )

    lines = store.search_lessons("教训", "c1", limit=8)
    joined = "\n".join(lines)
    assert "旧版教训" not in joined
    assert "新版教训" in joined


def test_search_lessons_filters_other_clients(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c2",
            text="其他租户教训：必须先给内部报价",
            fact_type="feedback",
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="本租户教训：必须先确认目标受众",
            fact_type="feedback",
        )
    )

    out = "\n".join(store.search_lessons("必须", "c1", limit=5))

    assert "本租户教训" in out
    assert "其他租户教训" not in out


def test_search_lessons_weight_count_in_total_weight(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="交付前必须二次核对关键数字。",
            fact_type="feedback",
            weight_count=2,
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="交付前必须二次核对关键数字。",
            fact_type="feedback",
            weight_count=3,
        )
    )
    out = store.search_lessons("交付", "c1", limit=3)
    assert len(out) == 1
    assert "同类×2" in out[0]
    assert "总权重×5" in out[0]


def test_supersedes_ignores_self_reference(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="自检：不要自引用 supersedes。",
            fact_type="feedback",
            supersedes_event_id="hst_self",
        )
    )
    data = json.loads(p.read_text(encoding="utf-8").strip())
    eid = data["event_id"]
    # 恶意自指：不得把自己标为 obsolete
    p.write_text("", encoding="utf-8")
    line = {**data, "supersedes_event_id": eid}
    p.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
    out = store.search_lessons("自检", "c1", limit=2)
    assert len(out) == 1


def test_append_lesson_accepts_supersedes_and_weight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_lesson(client_id="c1", text="lesson a", source="t")
    lid = json.loads(p.read_text(encoding="utf-8").strip())["event_id"]
    store.append_lesson(
        client_id="c1",
        text="lesson b",
        source="t",
        supersedes_event_id=lid,
        weight_count=4,
    )
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[1]["supersedes_event_id"] == lid
    assert rows[1]["weight_count"] == 4


def test_freq_merge_disable_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE", "0")
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for _ in range(2):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text="合并关闭时两条相同文本。",
                fact_type="feedback",
            )
        )
    out = store.search_lessons("合并", "c1", limit=5)
    assert not any("同类×" in x for x in out)
    assert len(out) == 2


def test_hindsight_search_bad_utf8_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    p.write_bytes(b"\xff\xfe\x00")
    store = HindsightStore(p)

    assert store.search_lessons("anything", "c1") == []
