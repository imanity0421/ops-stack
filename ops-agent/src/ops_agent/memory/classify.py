from __future__ import annotations

import re

from ops_agent.memory.models import MemoryLane

# 启发式：辅助选择 record_* 工具，不替代模型判断。
_TASK_HINTS = ("这次", "这篇", "本稿", "本方案", "本版", "交付物", "这版", "反馈", "稿子", "方案里")
_ATTR_HINTS = ("以后", "长期", "一直", "偏好", "不要", "客户是", "我们是", "公司名", "品类", "渠道")


def suggest_memory_lane(text: str) -> tuple[MemoryLane | None, str]:
    """
    根据用语粗分记忆槽：TASK_FEEDBACK vs ATTRIBUTE；不确定时 lane 为 None。
    """
    t = text.strip()
    if not t:
        return None, "空文本，无法分类。"

    task_score = sum(1 for h in _TASK_HINTS if h in t)
    attr_score = sum(1 for h in _ATTR_HINTS if h in t)

    # 强信号：明确任务反馈句式
    if re.search(r"(这|本)(次|篇|稿|版)", t):
        task_score += 2
    if re.search(r"以后|长期|一直", t):
        attr_score += 2

    if task_score > attr_score and task_score >= 1:
        return MemoryLane.TASK_FEEDBACK, f"启发式偏任务反馈（task={task_score}, attr={attr_score}）。"
    if attr_score > task_score and attr_score >= 1:
        return MemoryLane.ATTRIBUTE, f"启发式偏长期画像/偏好（task={task_score}, attr={attr_score}）。"
    return None, f"启发式不确定（task={task_score}, attr={attr_score}），请结合语义选择 record_*。"
