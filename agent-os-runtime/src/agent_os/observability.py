"""轻量可观测：从 Agno RunOutput 取工具名、token 粗算，拼成稳定可 grep 的日志行。

不引入额外依赖；格式前缀 ``AGENT_OS_OBS`` 便于生产 grep。
当 ``RunOutput.metrics`` 顶层 token 为 0 时，会尝试对 ``metrics.details`` 内各 ``ModelMetrics`` 求和（趋势用，非计费级）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

OBS_PREFIX = "AGENT_OS_OBS"


def tool_names_from_run_output(out: Any) -> list[str]:
    """从 ``RunOutput.tools`` 提取 ``tool_name``（去重、保序）。"""
    tools = getattr(out, "tools", None) or []
    seen: set[str] = set()
    names: list[str] = []
    for t in tools:
        n = getattr(t, "tool_name", None) or getattr(t, "name", None)
        s = str(n).strip() if n is not None else ""
        if s and s not in seen:
            seen.add(s)
            names.append(s)
    return names


def _sum_details_tokens(m: Any) -> tuple[int, int, int]:
    """从 ``RunMetrics.details`` 聚合各子模型 token（与 Agno 内部结构对齐）。"""
    it = ot = tt = 0
    details = getattr(m, "details", None)
    if not isinstance(details, dict):
        return (0, 0, 0)
    for _mt, mlist in details.items():
        if not isinstance(mlist, list):
            continue
        for mm in mlist:
            it += int(getattr(mm, "input_tokens", 0) or 0)
            ot += int(getattr(mm, "output_tokens", 0) or 0)
            tt += int(getattr(mm, "total_tokens", 0) or 0)
    return (it, ot, tt)


def _run_metrics_tokens(m: Any) -> tuple[int, int, int]:
    if m is None:
        return (0, 0, 0)
    it = int(getattr(m, "input_tokens", 0) or 0)
    ot = int(getattr(m, "output_tokens", 0) or 0)
    tt = int(getattr(m, "total_tokens", 0) or 0)
    if it or ot or tt:
        return (it, ot, tt)
    dit, dot, dtt = _sum_details_tokens(m)
    if dit or dot or dtt:
        if dtt == 0 and (dit or dot):
            dtt = dit + dot
        return (dit, dot, dtt)
    return (0, 0, 0)


def log_agent_run_obs(
    *,
    request_id: str,
    session_id: str,
    out: Any,
    elapsed_s: float,
    route: str = "/chat",
) -> str:
    """
    打一条 **INFO** 日志，并返回与日志**相同**的字符串（便于单测与响应头/调试端复用）。

    字段：request_id, session_id, model, tools, elapsed_ms, tok_in, tok_out, tot（粗算 token）。
    """
    model = getattr(out, "model", None) or ""
    m = getattr(out, "metrics", None)
    tin, tout, ttot = _run_metrics_tokens(m)
    tool_names = tool_names_from_run_output(out)
    tools_s = ";".join(tool_names) if tool_names else "-"
    ms = int(round(max(0.0, elapsed_s) * 1000))
    line = (
        f"{OBS_PREFIX} route={route} request_id={request_id} session_id={session_id} "
        f"model={model!r} tools={tools_s} elapsed_ms={ms} "
        f"tok_in={tin} tok_out={tout} tok_total={ttot}"
    )
    logger.info(line)
    return line


CTX_TRACE_PREFIX = "AGENT_OS_CONTEXT_TRACE"


def log_context_management_trace(
    *,
    request_id: str,
    session_id: str,
    trace: Any,
    route: str = "-",
) -> str:
    """P2-7：ContextBuilder 块级 trace，仅日志，不进 prompt。"""
    body = trace.to_obs_log_line()
    line = (
        f"{CTX_TRACE_PREFIX} route={route} request_id={request_id} "
        f"session_id={session_id} blocks={body}"
    )
    logger.info(line)
    return line


def grep_obs_line_pattern() -> re.Pattern[str]:
    """在测试中校验格式。"""
    return re.compile(
        r"^AGENT_OS_OBS route=\S+ request_id=\S+ session_id=\S+ model=.* tools=.* elapsed_ms=\d+ "
        r"tok_in=\d+ tok_out=\d+ tok_total=\d+"
    )
