from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_os.memory.models import UserFact

logger = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


def _normalize_merge_text(text: str) -> str:
    """用于同类合并：折叠空白 + strip + casefold。"""
    return _WS.sub(" ", (text or "").strip()).casefold()


def _merge_bucket_key(row: dict[str, Any]) -> str:
    typ = str(row.get("type") or "")
    return f"{typ}\n{_normalize_merge_text(str(row.get('text') or ''))}"


def _row_event_id(row: dict[str, Any]) -> str:
    eid = row.get("event_id")
    return str(eid).strip() if eid is not None else ""


def _row_weight(row: dict[str, Any]) -> int:
    try:
        w = int(row.get("weight_count", 1) or 1)
    except (TypeError, ValueError):
        w = 1
    return max(1, min(w, 10000))


def _recorded_epoch(row: dict[str, Any]) -> float:
    raw = row.get("recorded_at") or row.get("created_at")
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return 0.0
    s = raw.strip()
    if not s:
        return 0.0
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _freq_merge_enabled() -> bool:
    return os.getenv("AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE", "1").lower() not in (
        "0",
        "false",
        "no",
    )


def _score_hindsight_row(
    row: dict[str, Any],
    *,
    qtokens: set[str],
    user_id: str | None,
    task_id: str | None,
    skill_id: str | None,
    deliverable_type: str | None,
) -> float:
    text = str(row.get("text") or "")
    tokens = set(text.lower().split())
    score = float(len(qtokens & tokens) * 10 if qtokens else 1)
    if user_id and row.get("user_id") == user_id:
        score += 6.0
    if task_id and row.get("task_id") == task_id:
        score += 8.0
    if skill_id and row.get("skill_id") == skill_id:
        score += 4.0
    if deliverable_type and row.get("deliverable_type") == deliverable_type:
        score += 3.0
    try:
        if row.get("confidence") is not None:
            score += max(0.0, min(float(row.get("confidence")), 1.0)) * 2.0
    except (TypeError, ValueError):
        pass
    try:
        if row.get("outcome_score") is not None:
            score += max(0.0, min(float(row.get("outcome_score")), 1.0))
    except (TypeError, ValueError):
        pass
    if row.get("outcome") in ("success", "failure", "mixed") or row.get("is_success") is not None:
        score += 1.0
    recorded = row.get("recorded_at") or row.get("created_at") or "记录时间未知"
    try:
        dt = datetime.fromisoformat(str(recorded).replace("Z", "+00:00"))
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
        score += max(0.0, 2.0 - min(age_days / 90.0, 2.0))
    except Exception:
        pass
    return score


def _render_row(row: dict[str, Any], *, temporal_grounding: bool) -> str:
    text = str(row.get("text") or "")
    recorded = row.get("recorded_at") or row.get("created_at") or "记录时间未知"
    source = row.get("source") or "unknown"
    if temporal_grounding:
        return f"[记录于 {recorded} | 来源 {source}] {text}"
    return text


class HindsightStore:
    """
    任务反馈与复盘教训的本地存储（JSONL）。
    每行 JSON 至少含：type, client_id, text；type 为 feedback | lesson。

    检索侧支持：

    - **supersedes_event_id**：若行 B 含 ``supersedes_event_id`` 指向行 A 的 ``event_id``，则 A 在召回中隐藏（链式：多段取代只保留未被子取代者）。
    - **同类合并 / 频次**：规范化正文 + ``type`` 相同的行合并为一条展示，并在分数上给予对数加成；``weight_count`` 参与「总权重」展示。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append_feedback(self, fact: UserFact) -> dict[str, Any]:
        line: dict[str, Any] = {
            "memory_version": "2.0",
            "event_id": f"hst_{uuid4().hex}",
            "type": "feedback",
            "client_id": fact.client_id,
            "user_id": fact.user_id,
            "scope": fact.scope or "task_scoped",
            "skill_id": fact.skill_id,
            "task_id": fact.task_id,
            "deliverable_type": fact.deliverable_type,
            "text": fact.text,
            "impact_on_preference": fact.impact_on_preference,
            "confidence": fact.confidence,
            "outcome": fact.outcome,
            "outcome_score": fact.outcome_score,
            "is_success": fact.is_success,
            "conversion_rate": fact.conversion_rate,
            "tags": list(fact.tags),
            "evidence_refs": list(fact.evidence_refs),
            "recorded_at": fact.recorded_at.isoformat(),
            "source": fact.source,
        }
        if fact.supersedes_event_id:
            line["supersedes_event_id"] = str(fact.supersedes_event_id).strip()
        if fact.weight_count != 1:
            line["weight_count"] = int(fact.weight_count)
        self._append_line(line)
        return {"status": "ok", "path": str(self._path)}

    def append_lesson(
        self,
        *,
        client_id: str,
        text: str,
        user_id: str | None = None,
        task_id: str | None = None,
        skill_id: str | None = None,
        source: str = "async_review",
        confidence: float | None = None,
        tags: list[str] | None = None,
        supersedes_event_id: str | None = None,
        weight_count: int = 1,
    ) -> dict[str, Any]:
        wc = max(1, min(int(weight_count or 1), 10000))
        line: dict[str, Any] = {
            "memory_version": "2.0",
            "event_id": f"hst_{uuid4().hex}",
            "type": "lesson",
            "client_id": client_id,
            "user_id": user_id,
            "task_id": task_id,
            "skill_id": skill_id,
            "text": text.strip(),
            "source": source,
            "scope": "task_scoped" if task_id else "client_shared",
            "confidence": confidence,
            "tags": list(tags or []),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if supersedes_event_id:
            line["supersedes_event_id"] = str(supersedes_event_id).strip()
        if wc != 1:
            line["weight_count"] = wc
        self._append_line(line)
        return {"status": "ok", "path": str(self._path)}

    def _append_line(self, obj: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def search_lessons(
        self,
        query: str,
        client_id: str,
        limit: int = 8,
        *,
        user_id: str | None = None,
        task_id: str | None = None,
        skill_id: str | None = None,
        deliverable_type: str | None = None,
        temporal_grounding: bool = True,
    ) -> list[str]:
        """检索反馈与教训：租户硬过滤 + supersedes 隐藏 + 可选同类合并/频次加权。"""
        if not self._path.is_file():
            return []
        qtokens = set(query.lower().split())
        candidates: list[dict[str, Any]] = []
        try:
            lines = self._path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Hindsight 文件无法读取，返回空结果: %s (%s)", self._path, e)
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("client_id") != client_id:
                continue
            if row.get("type") not in ("feedback", "lesson"):
                continue
            text = row.get("text") or ""
            if not isinstance(text, str) or not text:
                continue
            candidates.append(row)

        obsolete: set[str] = set()
        for row in candidates:
            sid = row.get("supersedes_event_id")
            if isinstance(sid, str) and sid.strip():
                self_eid = _row_event_id(row)
                if self_eid and sid.strip() == self_eid:
                    continue
                obsolete.add(sid.strip())

        active = [r for r in candidates if _row_event_id(r) not in obsolete]

        if not _freq_merge_enabled():
            scored: list[tuple[float, str]] = []
            for row in active:
                sc = _score_hindsight_row(
                    row,
                    qtokens=qtokens,
                    user_id=user_id,
                    task_id=task_id,
                    skill_id=skill_id,
                    deliverable_type=deliverable_type,
                )
                rendered = _render_row(row, temporal_grounding=temporal_grounding)
                scored.append((sc, rendered))
            scored.sort(key=lambda x: -x[0])
            out: list[str] = []
            seen: set[str] = set()
            for _, t in scored:
                if t in seen:
                    continue
                seen.add(t)
                out.append(t)
                if len(out) >= limit:
                    break
            return out

        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in active:
            k = _merge_bucket_key(row)
            buckets.setdefault(k, []).append(row)

        merged_scored: list[tuple[float, str]] = []
        for _key, group in buckets.items():
            freq = len(group)
            total_w = sum(_row_weight(r) for r in group)
            best = max(
                group,
                key=lambda r: (
                    _score_hindsight_row(
                        r,
                        qtokens=qtokens,
                        user_id=user_id,
                        task_id=task_id,
                        skill_id=skill_id,
                        deliverable_type=deliverable_type,
                    ),
                    _recorded_epoch(r),
                ),
            )
            base = _score_hindsight_row(
                best,
                qtokens=qtokens,
                user_id=user_id,
                task_id=task_id,
                skill_id=skill_id,
                deliverable_type=deliverable_type,
            )
            bonus = min(4.0, math.log2(1.0 + float(freq)) * 1.25)
            if total_w > freq:
                bonus += min(2.0, 0.12 * float(total_w - freq))
            merged_sc = base + bonus
            rendered = _render_row(best, temporal_grounding=temporal_grounding)
            if freq > 1 or total_w > freq:
                rendered += f" （同类×{freq}，总权重×{total_w}）"
            merged_scored.append((merged_sc, rendered))

        merged_scored.sort(key=lambda x: -x[0])
        out2: list[str] = []
        seen2: set[str] = set()
        for _, t in merged_scored:
            if t in seen2:
                continue
            seen2.add(t)
            out2.append(t)
            if len(out2) >= limit:
                break
        return out2
