from __future__ import annotations

from ops_agent.memory.classify import suggest_memory_lane
from ops_agent.memory.models import MemoryLane


def test_suggest_task_feedback() -> None:
    lane, _ = suggest_memory_lane("这次方案标题太长了，改短一点")
    assert lane == MemoryLane.TASK_FEEDBACK


def test_suggest_attribute() -> None:
    lane, _ = suggest_memory_lane("以后发文都不要太正式，偏好轻松语气")
    assert lane == MemoryLane.ATTRIBUTE


def test_suggest_uncertain() -> None:
    lane, _ = suggest_memory_lane("好的")
    assert lane is None
