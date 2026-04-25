from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

EntryPoint = Literal["cli", "web", "api"]

_WEEKDAYS_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass(frozen=True)
class EphemeralContext:
    """当轮运行时上下文：只进 prompt，不进入长期记忆。"""

    now_utc: datetime
    timezone_name: str
    local_time_text: str
    weekday_text: str
    entrypoint: EntryPoint
    skill_id: str
    client_id: str
    user_id: str | None = None


def build_ephemeral_context(
    *,
    timezone_name: str,
    entrypoint: EntryPoint,
    skill_id: str,
    client_id: str,
    user_id: str | None,
    now_utc: datetime | None = None,
) -> EphemeralContext:
    """生成当轮临时上下文；timezone 非法时回退 UTC，避免启动失败。"""

    base = now_utc or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(timezone_name)
        tz_name = timezone_name
    except Exception:
        tz = timezone.utc
        tz_name = "UTC"
    local = base.astimezone(tz)
    return EphemeralContext(
        now_utc=base.astimezone(timezone.utc),
        timezone_name=tz_name,
        local_time_text=local.strftime("%Y-%m-%d %H:%M:%S %Z"),
        weekday_text=_WEEKDAYS_ZH[local.weekday()],
        entrypoint=entrypoint,
        skill_id=skill_id,
        client_id=client_id,
        user_id=user_id,
    )


def build_ephemeral_instruction(ctx: EphemeralContext) -> str:
    """格式化为系统指令片段，明确禁止自动沉淀为长期记忆。"""

    uid = ctx.user_id or "未指定"
    return (
        "【运行时临时上下文】\n"
        f"- 当前时间：{ctx.local_time_text}（{ctx.weekday_text}，时区 {ctx.timezone_name}）\n"
        f"- 入口：{ctx.entrypoint}\n"
        f"- 当前 skill_id：{ctx.skill_id}\n"
        f"- client_id：{ctx.client_id}；user_id：{uid}\n"
        "- 以上信息只用于本轮推理与排期判断，不代表长期客户事实；"
        "不得仅因本段内容调用记忆写入工具。"
    )
