from __future__ import annotations

from enum import Enum
from datetime import datetime, timezone
from typing import List, Literal

from pydantic import BaseModel, Field


class MemoryLane(str, Enum):
    """写入车道：ATTRIBUTE → Mem0；TASK_FEEDBACK → Hindsight（JSONL）。"""

    ATTRIBUTE = "mem0_attribute"
    TASK_FEEDBACK = "hindsight_feedback"


class UserFact(BaseModel):
    """经分类或工具抽取后、拟写入记忆的结构化事实。"""

    lane: MemoryLane
    client_id: str = Field(..., min_length=1, description="租户/客户隔离键")
    user_id: str | None = Field(None, description="终端用户，可选")
    task_id: str | None = None
    deliverable_type: str | None = None
    text: str = Field(..., min_length=1)
    fact_type: Literal["attribute", "preference", "feedback", "lesson"] = "attribute"
    source_message_id: str | None = None
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="系统记录该事实的事务时间（UTC）",
    )
    effective_at: datetime | None = Field(None, description="事实开始生效时间（可选）")
    expires_at: datetime | None = Field(None, description="事实预期过期时间（可选）")
    source: str = Field(
        "agent_tool", description="写入来源：agent_tool / ingest_api / async_review / manual 等"
    )
    confidence: float | None = Field(None, ge=0.0, le=1.0, description="可选置信度")
    impact_on_preference: bool = Field(
        False,
        description="若本条为任务反馈但同时影响长期偏好，在 Hindsight 记录中标记",
    )


class MemoryWriteResult(BaseModel):
    written_to: List[Literal["mem0", "hindsight"]] = Field(default_factory=list)
    dedup_skipped: bool = False
    dedup_reason: str | None = None
    policy_rejected: bool = False
    policy_reason: str | None = None


class MemorySearchHit(BaseModel):
    text: str
    metadata: dict = Field(default_factory=dict)
