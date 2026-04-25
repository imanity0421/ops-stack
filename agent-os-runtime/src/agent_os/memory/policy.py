from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agent_os.memory.models import MemoryLane, UserFact

_UNCERTAIN = re.compile(r"(可能|也许|大概|随便|暂时|这次先|先这样|开玩笑|玩笑|哈哈|不确定)")
_LONG_TERM = re.compile(r"(以后|长期|稳定|始终|默认|每次|所有|不要|必须|偏好|禁忌|固定|长期)")
_FEEDBACK = re.compile(
    r"(认为|反馈|不满意|不好|太|错误|问题|失败|教训|复盘|下次|以后|改进|严重|方向错|踩坑)"
)


class MemoryPolicyDecision(BaseModel):
    allow: bool
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    normalized_text: str | None = None


def evaluate_memory_write(fact: UserFact) -> MemoryPolicyDecision:
    """最小可解释策略：先防明显脏写入，复杂语义交给后续评测/模型 gate。"""

    text = re.sub(r"\s+", " ", fact.text or "").strip()
    if len(text) < 6:
        return MemoryPolicyDecision(allow=False, reason="too_short", confidence=0.95)
    if _UNCERTAIN.search(text):
        return MemoryPolicyDecision(allow=False, reason="uncertain_or_temporary", confidence=0.85)

    if fact.lane == MemoryLane.ATTRIBUTE:
        if fact.fact_type == "preference" and not _LONG_TERM.search(text):
            return MemoryPolicyDecision(
                allow=False,
                reason="preference_without_stable_signal",
                confidence=0.75,
            )
        return MemoryPolicyDecision(
            allow=True, reason="long_term_memory_candidate", confidence=0.7, normalized_text=text
        )

    if fact.lane == MemoryLane.TASK_FEEDBACK:
        if not _FEEDBACK.search(text):
            return MemoryPolicyDecision(
                allow=False,
                reason="feedback_without_actionable_signal",
                confidence=0.7,
            )
        return MemoryPolicyDecision(
            allow=True,
            reason="actionable_feedback_candidate",
            confidence=0.72,
            normalized_text=text,
        )

    return MemoryPolicyDecision(
        allow=True, reason="unknown_lane_allow", confidence=0.5, normalized_text=text
    )
