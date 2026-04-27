from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemoryLane, UserFact
from agent_os.review import async_review
from agent_os.review.async_review import AsyncReviewService, parse_review_lessons


def test_async_review_writes_through_memory_controller(monkeypatch, tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    ledger = tmp_path / "ledger.sqlite"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
        memory_ledger_path=ledger,
    )
    monkeypatch.setattr(
        async_review,
        "_extract_lessons",
        lambda transcript_text, model: "下次必须先确认关键约束，避免方案返工。",
    )

    service = AsyncReviewService(ctrl)
    service.submit_and_wait(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        transcript=[("user", "方案方向不对"), ("assistant", "我会调整")],
    )

    rows = [json.loads(x) for x in hindsight.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["type"] == "lesson"
    assert rows[0]["source"] == "async_review"
    assert rows[0]["event_at"]


def test_async_review_submit_and_wait_reports_timeout(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "local.json",
        hindsight_path=tmp_path / "hindsight.jsonl",
    )

    def slow_done(**kwargs):
        _ = kwargs
        t = threading.Thread(target=lambda: time.sleep(0.2))
        t.start()
        return t

    service = AsyncReviewService(ctrl)
    service.submit = slow_done  # type: ignore[method-assign]

    result = service.submit_and_wait(
        client_id="c1",
        user_id=None,
        task_id=None,
        transcript=[("user", "需要复盘这个任务"), ("assistant", "好的")],
        join_timeout_sec=0.01,
    )

    assert result["status"] == "timeout"


def test_async_review_structured_lessons_write_quality_scores(monkeypatch, tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    monkeypatch.setattr(
        async_review,
        "_extract_lessons",
        lambda transcript_text, model: (
            '{"lessons":[{"text":"下次必须先确认关键约束，避免方案返工。",'
            '"validity_score":0.85,"specificity_score":0.9}]}'
        ),
    )

    service = AsyncReviewService(ctrl)
    service.submit_and_wait(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        transcript=[("user", "方案方向不对"), ("assistant", "我会调整")],
    )

    rows = [json.loads(x) for x in hindsight.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["validity_score"] == 0.85
    assert rows[0]["specificity_score"] == 0.9


def test_parse_review_lessons_keeps_legacy_line_format() -> None:
    lessons = parse_review_lessons("1. 下次必须先确认关键约束，避免方案返工。")

    assert len(lessons) == 1
    assert lessons[0].text == "下次必须先确认关键约束，避免方案返工。"
    assert lessons[0].validity_score is None
    assert lessons[0].specificity_score is None


def test_parse_review_lessons_accepts_json_fence_and_clamps_scores() -> None:
    lessons = parse_review_lessons(
        '```json\n{"lessons":[{"text":"下次必须先确认关键约束，避免方案返工。",'
        '"validity_score":2,"specificity_score":-1}]}\n```'
    )

    assert len(lessons) == 1
    assert lessons[0].validity_score == 1.0
    assert lessons[0].specificity_score == 0.0


def test_parse_review_lessons_accepts_explicit_outcome() -> None:
    lessons = parse_review_lessons(
        '{"lessons":[{"text":"用户明确验收后，下次继续先给结论。",'
        '"validity_score":0.8,"specificity_score":0.9,'
        '"outcome":"success","outcome_score":1.2,"is_success":true}]}'
    )

    assert len(lessons) == 1
    assert lessons[0].outcome == "success"
    assert lessons[0].outcome_score == 1.0
    assert lessons[0].is_success is True


def test_async_review_derives_reinforcement_signals_from_similar_history(
    monkeypatch, tmp_path: Path
) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
        enable_memory_policy=False,
    )
    assert ctrl.hindsight_store is not None
    ctrl.hindsight_store.append_lesson(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        text="下次必须先确认关键约束，避免方案返工。",
        negative_evidence_count=2,
    )
    monkeypatch.setattr(
        async_review,
        "_extract_lessons",
        lambda transcript_text, model: (
            '{"lessons":[{"text":"下次必须先确认关键约束，避免方案返工。",'
            '"validity_score":0.9,"specificity_score":0.8}]}'
        ),
    )

    service = AsyncReviewService(ctrl)
    service.submit_and_wait(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        transcript=[("user", "方案方向不对"), ("assistant", "我会调整")],
    )

    rows = [json.loads(x) for x in hindsight.read_text(encoding="utf-8").splitlines()]
    latest = rows[-1]
    assert latest["recurrence_count"] >= 2
    assert latest["negative_evidence_count"] == 2
    assert latest["last_reinforced_at"]


def test_async_review_writes_candidate_outcome_when_explicit_in_review(
    monkeypatch, tmp_path: Path
) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    monkeypatch.setattr(
        async_review,
        "_extract_lessons",
        lambda transcript_text, model: (
            '{"lessons":[{"text":"用户明确验收后，下次继续先给结论。",'
            '"validity_score":0.8,"specificity_score":0.9,'
            '"outcome":"success","outcome_score":0.95,"is_success":true}]}'
        ),
    )

    service = AsyncReviewService(ctrl)
    service.submit_and_wait(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        transcript=[("user", "这版通过"), ("assistant", "收到")],
    )

    rows = [json.loads(x) for x in hindsight.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["outcome"] == "success"
    assert rows[0]["outcome_score"] == 0.95
    assert rows[0]["is_success"] is True


def test_async_review_explicit_upstream_outcome_overrides_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    monkeypatch.setattr(
        async_review,
        "_extract_lessons",
        lambda transcript_text, model: (
            '{"lessons":[{"text":"CI 失败时下次必须先看测试输出。",'
            '"outcome":"success","outcome_score":0.9,"is_success":true}]}'
        ),
    )

    service = AsyncReviewService(ctrl)
    service.submit_and_wait(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        transcript=[("user", "CI 挂了"), ("assistant", "我会修复")],
        outcome="failure",
        outcome_score=0.1,
        is_success=False,
    )

    rows = [json.loads(x) for x in hindsight.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["outcome"] == "failure"
    assert rows[0]["outcome_score"] == 0.1
    assert rows[0]["is_success"] is False


def test_async_review_outcome_and_reinforcement_time_semantics(monkeypatch, tmp_path: Path) -> None:
    hindsight = tmp_path / "hindsight.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "local.json",
        hindsight_path=hindsight,
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            user_id="u1",
            task_id="t1",
            text="复盘结论：发布前必须先跑回归测试。",
            fact_type="lesson",
            outcome="success",
        )
    )
    monkeypatch.setattr(
        async_review,
        "_extract_lessons",
        lambda transcript_text, model: (
            '{"lessons":[{"text":"复盘结论：发布前必须先跑完整回归测试。"}]}'
        ),
    )

    AsyncReviewService(ctrl).submit_and_wait(
        client_id="c1",
        user_id="u1",
        task_id="t1",
        transcript=[("user", "验收失败"), ("assistant", "以后先跑回归")],
        outcome="failure",
        outcome_score=0.2,
        is_success=False,
    )

    rows = [json.loads(x) for x in hindsight.read_text(encoding="utf-8").splitlines()]
    new_row = rows[-1]
    assert new_row["outcome"] == "failure"
    assert new_row["outcome_score"] == 0.2
    assert new_row["is_success"] is False
    assert new_row["recorded_at"] >= new_row["event_at"]
    assert rows[0]["recorded_at"] <= new_row["last_reinforced_at"] <= new_row["recorded_at"]
    assert new_row["recurrence_count"] >= 1
