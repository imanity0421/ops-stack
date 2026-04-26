from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


@dataclass(frozen=True)
class HindsightScore:
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HindsightRetrievalPolicy:
    """Append-only Hindsight 的召回评分策略。

    原始 Hindsight 行只增不减；本策略只决定召回排序。`supersedes_event_id`
    表示新版经验更优，默认作为旧行降权信号，而不是删除或硬隐藏旧行。
    """

    superseded_penalty: float = 6.0
    event_freshness_bonus: float = 2.0
    event_freshness_horizon_days: float = 90.0
    recorded_freshness_bonus: float = 0.5
    recorded_freshness_horizon_days: float = 180.0
    validity_bonus: float = 3.0
    specificity_bonus: float = 2.0
    recurrence_bonus_cap: float = 3.0
    negative_evidence_penalty_cap: float = 6.0
    last_reinforced_bonus: float = 1.0
    last_reinforced_horizon_days: float = 180.0
    success_outcome_bonus: float = 2.0
    failure_context_bonus: float = 0.5
    mixed_outcome_bonus: float = 0.75
    failure_recurrence_multiplier: float = 0.35
    mixed_recurrence_multiplier: float = 0.6

    def score_row(
        self,
        row: dict[str, Any],
        *,
        qtokens: set[str],
        user_id: str | None,
        task_id: str | None,
        skill_id: str | None,
        deliverable_type: str | None,
        superseded: bool = False,
    ) -> HindsightScore:
        text = str(row.get("text") or "")
        text_features = query_features(text)
        overlap = len(qtokens & text_features)
        score = float(overlap * 6 if qtokens else 1)
        reasons: list[str] = []
        if qtokens:
            reasons.append(f"query_overlap={overlap}")
        phrase_bonus = phrase_match_bonus(qtokens, text)
        if phrase_bonus:
            score += phrase_bonus
            reasons.append(f"phrase_match={phrase_bonus:.2f}")

        if user_id and row.get("user_id") == user_id:
            score += 6.0
            reasons.append("same_user")
        if task_id and row.get("task_id") == task_id:
            score += 8.0
            reasons.append("same_task")
        if skill_id and row.get("skill_id") == skill_id:
            score += 4.0
            reasons.append("same_skill")
        if deliverable_type and row.get("deliverable_type") == deliverable_type:
            score += 3.0
            reasons.append("same_deliverable")

        try:
            if row.get("confidence") is not None:
                bonus = max(0.0, min(float(row.get("confidence")), 1.0)) * 2.0
                score += bonus
                reasons.append(f"confidence={bonus:.2f}")
        except (TypeError, ValueError):
            pass

        outcome_kind = normalized_outcome(row)
        outcome_score = bounded_float(row.get("outcome_score"))
        if outcome_score is not None:
            if outcome_kind == "failure":
                bonus = max(0.0, 1.0 - outcome_score) * self.failure_context_bonus
                score += bonus
                reasons.append(f"failure_score_context={bonus:.2f}")
            else:
                bonus = outcome_score * self.success_outcome_bonus
                score += bonus
                reasons.append(f"outcome_score={bonus:.2f}")

        if outcome_kind == "success":
            score += self.success_outcome_bonus
            reasons.append("success_outcome")
        elif outcome_kind == "failure":
            score += self.failure_context_bonus
            reasons.append("failure_context")
        elif outcome_kind == "mixed":
            score += self.mixed_outcome_bonus
            reasons.append("mixed_outcome")

        validity = bounded_float(row.get("validity_score"))
        if validity is not None:
            bonus = validity * self.validity_bonus
            score += bonus
            reasons.append(f"validity={bonus:.2f}")

        specificity = bounded_float(row.get("specificity_score"))
        if specificity is not None:
            bonus = specificity * self.specificity_bonus
            score += bonus
            reasons.append(f"specificity={bonus:.2f}")

        recurrence = bounded_int(row.get("recurrence_count"), lower=1, upper=10000)
        if recurrence is not None and recurrence > 1:
            bonus = min(self.recurrence_bonus_cap, math_log_bonus(recurrence))
            if outcome_kind == "failure":
                bonus *= self.failure_recurrence_multiplier
            elif outcome_kind == "mixed":
                bonus *= self.mixed_recurrence_multiplier
            score += bonus
            reasons.append(f"recurrence={bonus:.2f}")

        negative_evidence = bounded_int(row.get("negative_evidence_count"), lower=0, upper=10000)
        if negative_evidence:
            penalty = min(self.negative_evidence_penalty_cap, 1.5 * float(negative_evidence))
            score -= penalty
            reasons.append(f"negative_evidence=-{penalty:.2f}")

        reinforced_bonus = freshness_bonus(
            temporal_epoch(row, "last_reinforced_at"),
            max_bonus=self.last_reinforced_bonus,
            horizon_days=self.last_reinforced_horizon_days,
        )
        if reinforced_bonus:
            score += reinforced_bonus
            reasons.append(f"last_reinforced={reinforced_bonus:.2f}")

        event_bonus = freshness_bonus(
            temporal_epoch(row, "event_at"),
            max_bonus=self.event_freshness_bonus,
            horizon_days=self.event_freshness_horizon_days,
        )
        if event_bonus:
            score += event_bonus
            reasons.append(f"event_freshness={event_bonus:.2f}")

        recorded_bonus = freshness_bonus(
            recorded_epoch(row),
            max_bonus=self.recorded_freshness_bonus,
            horizon_days=self.recorded_freshness_horizon_days,
        )
        if recorded_bonus:
            score += recorded_bonus
            reasons.append(f"recorded_freshness={recorded_bonus:.2f}")

        if superseded:
            score -= self.superseded_penalty
            reasons.append(f"superseded=-{self.superseded_penalty:.2f}")

        return HindsightScore(score=score, reasons=reasons)


DEFAULT_HINDSIGHT_RETRIEVAL_POLICY = HindsightRetrievalPolicy()


def query_features(text: str) -> set[str]:
    """生成中英文友好的轻量检索特征。

    英文/数字按词切分；中文连续片段补充整段、bigram 与 trigram，避免中文无空格
    query 在 ``split()`` 下完全失效。该函数仍是确定性轻量特征，不替代后续 embedding/rerank。
    """

    raw = (text or "").casefold()
    features = {w for w in _WORD_RE.findall(raw) if len(w) >= 2}
    for segment in _CJK_RE.findall(raw):
        if len(segment) <= 8:
            features.add(segment)
        for n in (2, 3):
            if len(segment) < n:
                continue
            features.update(segment[i : i + n] for i in range(0, len(segment) - n + 1))
    return features


def phrase_match_bonus(query_terms: set[str], text: str) -> float:
    if not query_terms:
        return 0.0
    compact_text = "".join((text or "").casefold().split())
    bonus = 0.0
    for term in query_terms:
        if len(term) >= 3 and term in compact_text:
            bonus += 4.0
    return min(bonus, 12.0)


def bounded_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return None


def bounded_int(value: Any, *, lower: int, upper: int) -> int | None:
    if value is None:
        return None
    try:
        return max(lower, min(int(value), upper))
    except (TypeError, ValueError):
        return None


def normalized_outcome(row: dict[str, Any]) -> str:
    outcome = str(row.get("outcome") or "").strip().casefold()
    if outcome in ("success", "failure", "mixed"):
        return outcome
    is_success = row.get("is_success")
    if is_success is True:
        return "success"
    if is_success is False:
        return "failure"
    score = bounded_float(row.get("outcome_score"))
    if score is not None:
        if score >= 0.8:
            return "success"
        if score <= 0.2:
            return "failure"
    return "unknown"


def math_log_bonus(value: int) -> float:
    import math

    return math.log2(1.0 + float(value))


def temporal_epoch(row: dict[str, Any], *fields: str) -> float:
    raw = None
    for field_name in fields:
        raw = row.get(field_name)
        if raw:
            break
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


def recorded_epoch(row: dict[str, Any]) -> float:
    return temporal_epoch(row, "recorded_at", "created_at")


def freshness_bonus(epoch: float, *, max_bonus: float, horizon_days: float) -> float:
    if epoch <= 0:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - epoch) / 86400)
    return max(0.0, max_bonus - min(age_days / horizon_days, max_bonus))
