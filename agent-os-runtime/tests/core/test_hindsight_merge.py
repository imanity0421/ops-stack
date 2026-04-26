"""Hindsight 同类合并、频次权重与 supersedes 链。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_os.memory.hindsight_index import HindsightDerivedIndex, route_hindsight_candidates
from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.hindsight_retrieval import query_features
from agent_os.memory.hindsight_vector import HindsightVectorIndex
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


def test_search_lessons_supersedes_demotes_obsolete_event(tmp_path: Path) -> None:
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
    assert "旧版教训" in joined
    assert "新版教训" in joined
    assert "新版教训" in lines[0]
    assert "旧版教训" in lines[1]


def test_search_lessons_supersedes_limit_can_exclude_demoted_event(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="旧版教训：先讲背景再进入正题。",
            fact_type="feedback",
        )
    )
    old_id = json.loads(p.read_text(encoding="utf-8").splitlines()[0])["event_id"]
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="新版教训：开头必须先抛冲突，再补背景。",
            fact_type="feedback",
            supersedes_event_id=old_id,
        )
    )

    lines = store.search_lessons("教训", "c1", limit=1)

    assert len(lines) == 1
    assert "新版教训" in lines[0]


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


def test_hindsight_vector_recall_uses_lancedb_candidate_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_embed(text: str, *, cfg) -> list[float]:
        _ = cfg
        return [1.0, 0.0] if any(x in text for x in ("CI", "回归", "测试", "验收")) else [0.0, 1.0]

    monkeypatch.setattr("agent_os.memory.hindsight_vector._embed_text_openai", fake_embed)
    p = tmp_path / "h.jsonl"
    store = HindsightStore(
        p,
        enable_vector_recall=True,
        vector_index_path=tmp_path / "hindsight_vector.lancedb",
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="开头教训：脚本必须先抛冲突，再补背景。",
            fact_type="feedback",
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="验收失败教训：发布前必须先跑完整回归测试。",
            fact_type="feedback",
        )
    )
    assert store.vector_index_status()["row_count"] == 2

    status = store.vector_index_status()
    assert status["fresh"] is True
    assert status["schema_version"] == 1

    out = store.search_lessons("CI 挂了", "c1", limit=1, debug_scores=True)

    assert len(out) == 1
    assert "验收失败教训" in out[0]
    assert "vector_distance=" in out[0]
    assert "vector_bonus=" in out[0]


def test_hindsight_vector_status_detects_stale_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agent_os.memory.hindsight_vector._embed_text_openai",
        lambda text, *, cfg: [1.0, 0.0],
    )
    p = tmp_path / "h.jsonl"
    store = HindsightStore(
        p,
        enable_vector_recall=True,
        vector_index_path=tmp_path / "hindsight_vector.lancedb",
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="教训：发布前必须先跑回归测试。",
            fact_type="feedback",
        )
    )
    assert store.vector_index_status()["fresh"] is True

    with p.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event_id": "manual",
                    "type": "feedback",
                    "client_id": "c1",
                    "text": "外部改写：必须确认约束。",
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    assert store.vector_index_status()["fresh"] is False


def test_hindsight_vector_append_upserts_same_event_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agent_os.memory.hindsight_vector._embed_text_openai",
        lambda text, *, cfg: [1.0, 0.0],
    )
    index = HindsightVectorIndex(path=tmp_path / "hindsight_vector.lancedb")
    row = {
        "event_id": "same-event",
        "type": "feedback",
        "client_id": "c1",
        "text": "教训：发布前必须先跑回归测试。",
    }
    sig = {"size": 10, "mtime_ns": 20}

    index.append(row, source_path="h.jsonl", source_signature=sig)
    index.append(row, source_path="h.jsonl", source_signature=sig)
    status = index.status(source_path="h.jsonl", source_signature=sig)

    assert status["row_count"] == 1
    assert status["fresh"] is True


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


def test_search_lessons_chinese_query_without_spaces_matches_subphrase(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="复盘结论：交付前必须确认关键约束并复述用户目标。",
            fact_type="feedback",
        )
    )

    out = store.search_lessons("关键约束", "c1", limit=1)

    assert out
    assert "确认关键约束" in out[0]


def test_search_lessons_limits_superseded_budget(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    old_ids: list[str] = []
    for i in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text=f"旧版教训{i}：复盘时需要先写背景。",
                fact_type="feedback",
            )
        )
        old_ids.append(json.loads(p.read_text(encoding="utf-8").splitlines()[-1])["event_id"])
    for i, old_id in enumerate(old_ids):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text=f"新版教训{i}：复盘时必须先给结论，再补背景。",
                fact_type="feedback",
                supersedes_event_id=old_id,
            )
        )

    out = store.search_lessons("复盘 背景", "c1", limit=4)

    assert len(out) == 4
    assert sum("旧版教训" in x for x in out) <= 1


def test_search_lessons_relaxes_duplicate_cluster_budget_when_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE", "0")
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for _ in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text="重复教训：复盘前必须确认关键约束。",
                fact_type="feedback",
            )
        )

    out = store.search_lessons("复盘 约束", "c1", limit=4)

    assert len(out) == 4


def test_search_lessons_limits_unrequested_skill_budget(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for i in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                skill_id="skill_a",
                text=f"技能A教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )
    for i in range(2):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                skill_id="skill_b",
                text=f"技能B教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )

    out = store.search_lessons("复盘 约束", "c1", limit=4)

    assert len(out) == 4
    assert sum("技能A教训" in x for x in out) <= 3
    assert any("技能B教训" in x for x in out)


def test_search_lessons_requested_skill_is_not_budget_limited(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for i in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                skill_id="skill_a",
                text=f"技能A教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )

    out = store.search_lessons("复盘 约束", "c1", skill_id="skill_a", limit=4)

    assert len(out) == 4
    assert all("技能A教训" in x for x in out)


def test_search_lessons_limits_unrequested_deliverable_budget(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for i in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                deliverable_type="script",
                text=f"脚本教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )
    for i in range(2):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                deliverable_type="brief",
                text=f"简报教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )

    out = store.search_lessons("复盘 约束", "c1", limit=4)

    assert len(out) == 4
    assert sum("脚本教训" in x for x in out) <= 3
    assert any("简报教训" in x for x in out)


def test_hindsight_derived_index_routes_relevant_cluster_before_noise() -> None:
    rows = [
        {
            "client_id": "c1",
            "type": "lesson",
            "text": f"噪声教训{i}：日常沟通要保持礼貌。",
            "recorded_at": f"2026-04-25T00:{i % 60:02d}:00+00:00",
        }
        for i in range(120)
    ]
    rows.append(
        {
            "client_id": "c1",
            "type": "lesson",
            "text": "关键教训：交付前必须确认关键约束并复述用户目标。",
            "recorded_at": "2026-04-26T00:00:00+00:00",
        }
    )

    routed = route_hindsight_candidates(
        rows,
        query_terms=query_features("关键约束"),
        user_id=None,
        task_id=None,
        skill_id=None,
        deliverable_type=None,
        max_rows=20,
    )

    assert len(routed) == 20
    assert any("关键教训" in str(r.get("text")) for r in routed)


def test_hindsight_derived_index_groups_near_duplicate_lessons() -> None:
    rows = [
        {
            "client_id": "c1",
            "type": "lesson",
            "text": "交付前必须确认关键约束并复述用户目标。",
        },
        {
            "client_id": "c1",
            "type": "lesson",
            "text": "交付前必须确认关键约束，并且复述用户目标。",
        },
        {
            "client_id": "c1",
            "type": "lesson",
            "text": "会议纪要要先列行动项。",
        },
    ]

    index = HindsightDerivedIndex.build(rows)
    cluster_sizes = sorted(len(cluster.rows) for cluster in index.clusters)

    assert cluster_sizes == [1, 2]


def test_search_lessons_uses_persistent_index_after_first_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="关键教训：交付前必须确认关键约束并复述用户目标。",
            fact_type="feedback",
        )
    )
    assert store.search_lessons("关键约束", "c1", limit=1)
    assert p.with_name(f"{p.name}.index.json").is_file()

    def fail_read_jsonl_rows(path: Path) -> list[dict[str, object]]:
        raise AssertionError(f"should use persistent index, got {path}")

    monkeypatch.setattr(
        "agent_os.memory.hindsight_store._read_jsonl_rows",
        fail_read_jsonl_rows,
    )

    out = store.search_lessons("关键约束", "c1", limit=1)

    assert out
    assert "关键教训" in out[0]


def test_append_updates_existing_persistent_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="旧教训：交付前确认目标。",
            fact_type="feedback",
        )
    )
    assert store.search_lessons("目标", "c1", limit=1)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="新增教训：交付前必须确认关键约束。",
            fact_type="feedback",
        )
    )

    def fail_read_jsonl_rows(path: Path) -> list[dict[str, object]]:
        raise AssertionError(f"should use incrementally updated index, got {path}")

    monkeypatch.setattr(
        "agent_os.memory.hindsight_store._read_jsonl_rows",
        fail_read_jsonl_rows,
    )

    out = store.search_lessons("关键约束", "c1", limit=1)

    assert out
    assert "新增教训" in out[0]


def test_hindsight_store_invalidate_index_removes_sidecar(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="敏感教训：交付前必须确认关键约束。",
            fact_type="feedback",
        )
    )
    assert store.search_lessons("关键约束", "c1", limit=1)
    index_path = p.with_name(f"{p.name}.index.json")
    assert index_path.is_file()

    removed = store.invalidate_index()

    assert removed is True
    assert not index_path.exists()


def test_hindsight_store_index_status_and_rebuild(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="运维教训：交付前必须确认关键约束。",
            fact_type="feedback",
        )
    )

    assert store.index_status()["index_exists"] is False
    rebuilt = store.rebuild_index()
    status = store.index_status()

    assert rebuilt["status"] == "ok"
    assert rebuilt["row_count"] == 1
    assert status["index_exists"] is True
    assert status["fresh"] is True
    assert status["row_count"] == 1


def test_reinforcement_signals_from_similar_history(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            user_id="u1",
            task_id="t1",
            text="复盘结论：交付前必须确认关键约束并复述用户目标。",
            fact_type="feedback",
            negative_evidence_count=1,
        )
    )

    signals = store.reinforcement_signals(
        text="交付前必须确认关键约束并复述用户目标。",
        client_id="c1",
        user_id="u1",
        task_id="t1",
    )

    assert signals.recurrence_count is not None
    assert signals.recurrence_count >= 2
    assert signals.negative_evidence_count == 1
    assert signals.last_reinforced_at is not None


def test_search_lessons_uses_derived_index_when_candidate_pool_is_large(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for i in range(120):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                text=f"噪声教训{i}：日常沟通要保持礼貌。",
                fact_type="feedback",
            )
        )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="关键教训：交付前必须确认关键约束并复述用户目标。",
            fact_type="feedback",
        )
    )

    out = store.search_lessons("关键约束", "c1", limit=3)

    assert any("关键教训" in x for x in out)


def test_search_lessons_debug_scores_includes_reasons(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            user_id="u1",
            skill_id="default_agent",
            text="复盘结论：交付前必须确认关键约束。",
            fact_type="feedback",
            confidence=0.8,
        )
    )

    out = store.search_lessons(
        "关键约束",
        "c1",
        user_id="u1",
        skill_id="default_agent",
        limit=1,
        debug_scores=True,
    )

    assert "score=" in out[0]
    assert "reasons=" in out[0]
    assert "same_user" in out[0]
    assert "same_skill" in out[0]


def test_experience_quality_scores_can_promote_specific_valid_lesson(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="泛化教训：交付前必须确认关键约束。",
            fact_type="feedback",
            validity_score=0.1,
            specificity_score=0.1,
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="具体教训：交付前必须确认关键约束，并复述用户目标与验收标准。",
            fact_type="feedback",
            validity_score=1.0,
            specificity_score=1.0,
            recurrence_count=4,
            last_reinforced_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
        )
    )

    out = store.search_lessons("关键约束", "c1", limit=2, debug_scores=True)

    assert "具体教训" in out[0]
    assert "validity=" in out[0]
    assert "specificity=" in out[0]
    assert "recurrence=" in out[0]
    assert "last_reinforced=" in out[0]


def test_negative_evidence_demotes_lesson(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="高反证教训：交付前必须确认关键约束。",
            fact_type="feedback",
            validity_score=1.0,
            specificity_score=1.0,
            negative_evidence_count=4,
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="低反证教训：交付前必须确认关键约束。",
            fact_type="feedback",
            validity_score=0.4,
            specificity_score=0.4,
            negative_evidence_count=0,
        )
    )

    out = store.search_lessons("关键约束", "c1", limit=2, debug_scores=True)

    assert "低反证教训" in out[0]
    assert "negative_evidence=" in "\n".join(out)


def test_failure_recurrence_is_not_amplified_like_success(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="失败教训：交付前必须确认关键约束。",
            fact_type="feedback",
            outcome="failure",
            outcome_score=0.0,
            recurrence_count=100,
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="成功经验：交付前必须确认关键约束。",
            fact_type="feedback",
            outcome="success",
            outcome_score=1.0,
            recurrence_count=2,
        )
    )

    out = store.search_lessons("关键约束", "c1", limit=2, debug_scores=True)

    assert "成功经验" in out[0]
    assert "success_outcome" in out[0]
    assert "failure_context" in "\n".join(out)


def test_search_lessons_limits_unrequested_user_budget(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for i in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                user_id="u1",
                text=f"用户A教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )
    for i in range(2):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                user_id="u2",
                text=f"用户B教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )

    out = store.search_lessons("复盘 约束", "c1", limit=4)

    assert len(out) == 4
    assert sum("用户A教训" in x for x in out) <= 3
    assert any("用户B教训" in x for x in out)


def test_search_lessons_requested_task_is_not_budget_limited(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    for i in range(4):
        store.append_feedback(
            UserFact(
                lane=MemoryLane.TASK_FEEDBACK,
                client_id="c1",
                task_id="task_a",
                text=f"任务A教训{i}：复盘时必须确认关键约束。",
                fact_type="feedback",
            )
        )

    out = store.search_lessons("复盘 约束", "c1", task_id="task_a", limit=4)

    assert len(out) == 4
    assert all("任务A教训" in x for x in out)


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


def test_hindsight_scores_event_time_separately_from_recorded_time(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    store = HindsightStore(p)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="复盘结论：交付前必须确认关键约束。",
            fact_type="feedback",
            event_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
        )
    )
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="复盘结论：交付前必须确认关键约束并复述用户目标。",
            fact_type="feedback",
            event_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
            recorded_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
    )

    out = store.search_lessons("交付 约束", "c1", limit=2)

    assert "复述用户目标" in out[0]


def test_hindsight_search_bad_utf8_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    p.write_bytes(b"\xff\xfe\x00")
    store = HindsightStore(p)

    assert store.search_lessons("anything", "c1") == []
