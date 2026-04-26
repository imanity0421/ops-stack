from __future__ import annotations

from enum import Enum
from datetime import datetime, timezone
from typing import List, Literal

from pydantic import BaseModel, Field


class MemoryLane(str, Enum):
    """写入车道：ATTRIBUTE → Mem0；TASK_FEEDBACK → Hindsight（JSONL）。"""

    ATTRIBUTE = "mem0_attribute"
    TASK_FEEDBACK = "hindsight_feedback"


CLIENT_SHARED_USER_ID = "__client_shared__"
MemoryScope = Literal["system", "client_shared", "user_private", "task_scoped"]
HindsightOutcome = Literal["success", "failure", "mixed", "unknown"]


class UserFact(BaseModel):
    """经分类或工具抽取后、拟写入记忆的结构化事实。"""

    lane: MemoryLane
    client_id: str = Field(..., min_length=1, description="租户或工作区隔离键")
    user_id: str | None = Field(None, description="终端用户，可选")
    scope: MemoryScope | None = Field(
        None,
        description="记忆作用域；未传时由 MemoryController 根据 lane/user_id 推导",
    )
    skill_id: str | None = Field(
        None, description="可选 skill 标签，用于 Hindsight/Asset 等召回加权"
    )
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
    outcome: HindsightOutcome | None = Field(None, description="任务结果信号，可选")
    outcome_score: float | None = Field(None, ge=0.0, le=1.0, description="任务结果分，越高越成功")
    is_success: bool | None = Field(None, description="兼容显式成功/失败信号")
    conversion_rate: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="可选转化率；仅适用于有明确转化指标的任务",
    )
    tags: list[str] = Field(default_factory=list, description="经验标签")
    evidence_refs: list[str] = Field(default_factory=list, description="证据或上游反馈 id")
    supersedes_event_id: str | None = Field(
        None,
        description="Hindsight 的 event_id：写入时表示本条取代该事件（被取代行在检索中隐藏）",
    )
    weight_count: int = Field(
        1,
        ge=1,
        le=10000,
        description="检索合并时的权重（默认 1）；与同类行数一起计入总权重展示",
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
