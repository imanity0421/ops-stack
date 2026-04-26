from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from agent_os.memory.models import MemoryLane, UserFact

_UNCERTAIN = re.compile(r"(可能|也许|大概|随便|暂时|这次先|先这样|开玩笑|玩笑|哈哈|不确定)")
_LONG_TERM = re.compile(r"(以后|长期|稳定|始终|默认|每次|所有|不要|必须|偏好|禁忌|固定|长期)")
_FEEDBACK = re.compile(
    r"(认为|反馈|不满意|不好|太|错误|问题|失败|教训|复盘|下次|以后|改进|严重|方向错|踩坑)"
)
_LESSON = re.compile(r"(必须|不要|避免|优先|先|需要|应当|下次|教训|复盘|改进|注意)")
_SECRET = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)

PolicyCategory = Literal[
    "secret",
    "temporary_or_uncertain",
    "stable_preference",
    "long_term_fact",
    "task_feedback",
    "actionable_lesson",
    "low_signal",
    "unknown",
]
PolicySeverity = Literal["block", "warn", "allow"]


class MemoryPolicyDecision(BaseModel):
    allow: bool
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    normalized_text: str | None = None
    category: PolicyCategory = "unknown"
    severity: PolicySeverity = "allow"
    rule_id: str | None = None
    matched_signals: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PolicyEvalCase:
    case_id: str
    fact: UserFact
    expected_allow: bool
    expected_category: PolicyCategory


@dataclass(frozen=True)
class PolicyEvalReport:
    total: int
    passed: int
    failed_case_ids: list[str]

    @property
    def pass_rate(self) -> float:
        return 1.0 if self.total == 0 else self.passed / float(self.total)


def _decision(
    *,
    allow: bool,
    reason: str,
    confidence: float,
    normalized_text: str | None,
    category: PolicyCategory,
    severity: PolicySeverity,
    rule_id: str,
    matched_signals: list[str] | None = None,
) -> MemoryPolicyDecision:
    return MemoryPolicyDecision(
        allow=allow,
        reason=reason,
        confidence=confidence,
        normalized_text=normalized_text,
        category=category,
        severity=severity,
        rule_id=rule_id,
        matched_signals=matched_signals or [],
    )


def evaluate_memory_write(fact: UserFact) -> MemoryPolicyDecision:
    """最小可解释策略：先防明显脏写入，复杂语义交给后续评测/模型 gate。"""

    text = re.sub(r"\s+", " ", fact.text or "").strip()
    if len(text) < 6:
        return _decision(
            allow=False,
            reason="too_short",
            confidence=0.95,
            normalized_text=text,
            category="low_signal",
            severity="block",
            rule_id="POLICY_TOO_SHORT",
        )
    if _SECRET.search(text):
        return _decision(
            allow=False,
            reason="secret_like_content",
            confidence=0.95,
            normalized_text=text,
            category="secret",
            severity="block",
            rule_id="POLICY_SECRET_LIKE",
            matched_signals=["secret_like_pattern"],
        )
    if _UNCERTAIN.search(text):
        return _decision(
            allow=False,
            reason="uncertain_or_temporary",
            confidence=0.85,
            normalized_text=text,
            category="temporary_or_uncertain",
            severity="block",
            rule_id="POLICY_TEMPORARY_UNCERTAIN",
            matched_signals=["uncertain_or_temporary_terms"],
        )

    if fact.lane == MemoryLane.ATTRIBUTE:
        if fact.fact_type == "preference" and not _LONG_TERM.search(text):
            return _decision(
                allow=False,
                reason="preference_without_stable_signal",
                confidence=0.75,
                normalized_text=text,
                category="low_signal",
                severity="block",
                rule_id="POLICY_UNSTABLE_PREFERENCE",
            )
        category: PolicyCategory = (
            "stable_preference" if fact.fact_type == "preference" else "long_term_fact"
        )
        return _decision(
            allow=True,
            reason="long_term_memory_candidate",
            confidence=0.7,
            normalized_text=text,
            category=category,
            severity="allow",
            rule_id="POLICY_LONG_TERM_MEMORY",
            matched_signals=["long_term_terms"] if _LONG_TERM.search(text) else [],
        )

    if fact.lane == MemoryLane.TASK_FEEDBACK:
        if not (_FEEDBACK.search(text) or (fact.fact_type == "lesson" and _LESSON.search(text))):
            return _decision(
                allow=False,
                reason="feedback_without_actionable_signal",
                confidence=0.7,
                normalized_text=text,
                category="low_signal",
                severity="block",
                rule_id="POLICY_LOW_SIGNAL_FEEDBACK",
            )
        return _decision(
            allow=True,
            reason="actionable_feedback_candidate",
            confidence=0.72,
            normalized_text=text,
            category="actionable_lesson" if fact.fact_type == "lesson" else "task_feedback",
            severity="allow",
            rule_id="POLICY_ACTIONABLE_FEEDBACK",
            matched_signals=["feedback_terms"] if _FEEDBACK.search(text) else ["lesson_terms"],
        )

    return _decision(
        allow=True,
        reason="unknown_lane_allow",
        confidence=0.5,
        normalized_text=text,
        category="unknown",
        severity="warn",
        rule_id="POLICY_UNKNOWN_LANE",
    )


POLICY_EVAL_CASES: tuple[PolicyEvalCase, ...] = (
    PolicyEvalCase(
        case_id="secret_api_key",
        fact=UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="eval",
            text="默认 API_KEY 是 sk-abcdefghijklmnop",
        ),
        expected_allow=False,
        expected_category="secret",
    ),
    PolicyEvalCase(
        case_id="temporary_joke",
        fact=UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="eval",
            text="哈哈我开玩笑的，暂时随便说说",
        ),
        expected_allow=False,
        expected_category="temporary_or_uncertain",
    ),
    PolicyEvalCase(
        case_id="stable_preference",
        fact=UserFact(
            lane=MemoryLane.ATTRIBUTE,
            client_id="eval",
            text="以后所有交付物默认不要使用夸张表述",
            fact_type="preference",
        ),
        expected_allow=True,
        expected_category="stable_preference",
    ),
    PolicyEvalCase(
        case_id="task_feedback",
        fact=UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="eval",
            text="用户认为方案方向错了，下次先确认关键约束",
            fact_type="feedback",
        ),
        expected_allow=True,
        expected_category="task_feedback",
    ),
    PolicyEvalCase(
        case_id="low_signal_feedback",
        fact=UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="eval",
            text="挺好的",
            fact_type="feedback",
        ),
        expected_allow=False,
        expected_category="low_signal",
    ),
)


def evaluate_policy_cases(cases: tuple[PolicyEvalCase, ...] = POLICY_EVAL_CASES) -> PolicyEvalReport:
    failed: list[str] = []
    for case in cases:
        decision = evaluate_memory_write(case.fact)
        if decision.allow != case.expected_allow or decision.category != case.expected_category:
            failed.append(case.case_id)
    return PolicyEvalReport(total=len(cases), passed=len(cases) - len(failed), failed_case_ids=failed)
