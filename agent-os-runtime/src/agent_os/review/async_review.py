from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, cast

from openai import OpenAI

from agent_os.memory.controller import MemoryController
from agent_os.memory.models import HindsightOutcome, MemoryLane, UserFact

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewLessonCandidate:
    text: str
    validity_score: float | None = None
    specificity_score: float | None = None
    outcome: HindsightOutcome | None = None
    outcome_score: float | None = None
    is_success: bool | None = None


def _bounded_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return None


def _normalize_outcome(value: Any) -> HindsightOutcome | None:
    text = str(value or "").strip().casefold()
    if text in ("success", "failure", "mixed", "unknown"):
        return cast(HindsightOutcome, text)
    return None


def _normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in ("1", "true", "yes", "y", "success", "succeeded"):
        return True
    if text in ("0", "false", "no", "n", "failure", "failed"):
        return False
    return None


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _candidate_from_obj(obj: Any) -> ReviewLessonCandidate | None:
    if isinstance(obj, str):
        text = obj.strip().lstrip("0123456789.-、)） ")
        return ReviewLessonCandidate(text=text) if len(text) >= 8 else None
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("text") or obj.get("lesson") or obj.get("lesson_text") or "").strip()
    if len(text) < 8:
        return None
    return ReviewLessonCandidate(
        text=text,
        validity_score=_bounded_score(obj.get("validity_score")),
        specificity_score=_bounded_score(obj.get("specificity_score")),
        outcome=_normalize_outcome(obj.get("outcome")),
        outcome_score=_bounded_score(obj.get("outcome_score")),
        is_success=_normalize_bool(obj.get("is_success")),
    )


def parse_review_lessons(raw: str) -> list[ReviewLessonCandidate]:
    text = _strip_json_fence(raw)
    if not text or text == "无" or text.startswith("无。"):
        return []
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None

    if isinstance(obj, dict):
        data = obj.get("lessons")
        if data is None:
            data = obj.get("items")
        if data is None:
            data = [obj]
    elif isinstance(obj, list):
        data = obj
    else:
        data = None

    if isinstance(data, list):
        candidates = [_candidate_from_obj(x) for x in data]
        return [x for x in candidates if x is not None][:3]

    candidates: list[ReviewLessonCandidate] = []
    for block in text.split("\n"):
        item = _candidate_from_obj(block)
        if item is not None:
            candidates.append(item)
        if len(candidates) >= 3:
            break
    return candidates


def transcript_to_text(transcript: list[tuple[str, str]]) -> str:
    """将 (role, content) 列表转为可读对话文本。"""
    lines: list[str] = []
    for role, content in transcript:
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_lessons(transcript_text: str, model: str | None) -> str:
    mid = model or os.getenv("AGENT_OS_MODEL", "gpt-4o-mini")
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE") or None,
    )
    prompt = (
        "你是任务复盘助手。根据以下对话，总结 1～3 条可复用的「教训/注意事项」。"
        "不要重复已写在 Mem0 的静态事实，不要记录一次性临时要求。"
        "若无值得记录的教训，输出单独一行：无。否则输出严格 JSON："
        '{"lessons":[{"text":"中文教训","validity_score":0.0,"specificity_score":0.0,'
        '"outcome":"unknown","outcome_score":null}]}。'
        "validity_score 表示这条教训是否可信、可复用；specificity_score 表示是否具体可执行。"
        "两个分数必须在 0 到 1 之间。仅当对话里有明确验收、失败或部分成功证据时，"
        "outcome 才能填 success/failure/mixed；否则必须填 unknown，outcome_score 填 null。\n\n对话：\n"
        f"{transcript_text[:12000]}"
    )
    r = client.chat.completions.create(
        model=mid,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return (r.choices[0].message.content or "").strip()


class AsyncReviewService:
    """会话结束后异步写入 Hindsight（不阻塞 CLI 退出）。"""

    def __init__(
        self,
        controller: MemoryController,
        *,
        enabled: bool = True,
        model: str | None = None,
    ) -> None:
        self._controller = controller
        self._enabled = enabled
        self._model = model

    @classmethod
    def from_env(cls, controller: MemoryController) -> AsyncReviewService:
        return cls(
            controller,
            enabled=os.getenv("AGENT_OS_ASYNC_REVIEW_ON_EXIT", "1").lower()
            not in ("0", "false", "no"),
            model=os.getenv("AGENT_OS_ASYNC_REVIEW_MODEL"),
        )

    def submit(
        self,
        *,
        client_id: str,
        user_id: str | None,
        task_id: str | None,
        transcript: list[tuple[str, str]],
        outcome: HindsightOutcome | None = None,
        outcome_score: float | None = None,
        is_success: bool | None = None,
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> threading.Thread | None:
        if not self._enabled:
            logger.info("AsyncReview 已关闭 (AGENT_OS_ASYNC_REVIEW_ON_EXIT)")
            return None
        if not transcript:
            return None
        text_blob = transcript_to_text(transcript)
        if len(text_blob) < 20:
            return None
        event_at = datetime.now(timezone.utc)

        def worker() -> None:
            try:
                raw = _extract_lessons(text_blob, self._model)
                candidates = parse_review_lessons(raw)
                if not candidates:
                    logger.info("AsyncReview: 模型认为无需写入教训")
                    if on_done:
                        on_done({"status": "skipped"})
                    return
                for candidate in candidates:
                    signals = None
                    if self._controller.hindsight_store is not None:
                        signals = self._controller.hindsight_store.reinforcement_signals(
                            text=candidate.text,
                            client_id=client_id,
                            user_id=user_id,
                            task_id=task_id,
                            observed_at=event_at,
                        )
                    r = self._controller.ingest_user_fact(
                        UserFact(
                            lane=MemoryLane.TASK_FEEDBACK,
                            client_id=client_id,
                            user_id=user_id,
                            task_id=task_id,
                            text=candidate.text,
                            fact_type="lesson",
                            event_at=event_at,
                            source="async_review",
                            confidence=0.7,
                            validity_score=candidate.validity_score,
                            specificity_score=candidate.specificity_score,
                            outcome=outcome or candidate.outcome,
                            outcome_score=_bounded_score(outcome_score)
                            if outcome_score is not None
                            else candidate.outcome_score,
                            is_success=is_success
                            if is_success is not None
                            else candidate.is_success,
                            recurrence_count=signals.recurrence_count if signals else None,
                            negative_evidence_count=signals.negative_evidence_count
                            if signals
                            else None,
                            last_reinforced_at=signals.last_reinforced_at if signals else None,
                        )
                    )
                    if r.policy_rejected:
                        logger.info(
                            "AsyncReview: lesson 被 MemoryPolicy 拒绝 reason=%s text=%s",
                            r.policy_reason,
                            candidate.text[:80],
                        )
                    elif r.dedup_skipped:
                        logger.info(
                            "AsyncReview: lesson 去重跳过 reason=%s text=%s",
                            r.dedup_reason,
                            candidate.text[:80],
                        )
                logger.info("AsyncReview: 已通过 MemoryController 处理 Hindsight 候选")
                if on_done:
                    on_done({"status": "ok"})
            except Exception as e:
                logger.exception("AsyncReview 失败: %s", e)
                if on_done:
                    on_done({"status": "error", "error": str(e)})

        t = threading.Thread(target=worker, name="agent-os-runtime-async-review", daemon=False)
        t.start()
        return t

    def submit_and_wait(
        self,
        *,
        client_id: str,
        user_id: str | None,
        task_id: str | None,
        transcript: list[tuple[str, str]],
        outcome: HindsightOutcome | None = None,
        outcome_score: float | None = None,
        is_success: bool | None = None,
        join_timeout_sec: float = 120.0,
    ) -> dict[str, Any]:
        """同步等待复盘线程结束（CLI 退出前调用，避免进程过早退出导致复盘中断）。"""
        result: dict[str, Any] = {}

        def on_done(payload: dict[str, Any]) -> None:
            result.update(payload)

        t = self.submit(
            client_id=client_id,
            user_id=user_id,
            task_id=task_id,
            transcript=transcript,
            outcome=outcome,
            outcome_score=outcome_score,
            is_success=is_success,
            on_done=on_done,
        )
        if t is None:
            return {"status": "skipped"}
        t.join(timeout=join_timeout_sec)
        if t.is_alive():
            return {"status": "timeout", "timeout_sec": join_timeout_sec}
        return result or {"status": "completed"}
