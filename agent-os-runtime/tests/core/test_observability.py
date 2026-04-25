from __future__ import annotations

from agno.metrics import RunMetrics

from agent_os.observability import (
    grep_obs_line_pattern,
    log_agent_run_obs,
    tool_names_from_run_output,
)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.tool_name = name


class _FakeOut:
    def __init__(self) -> None:
        self.model = "gpt-4o-mini"
        self.tools = [_FakeTool("retrieve_ordered_context"), _FakeTool("search_client_memory")]
        self.metrics = RunMetrics(input_tokens=10, output_tokens=5, total_tokens=15)


def test_tool_names_from_run_output() -> None:
    o = _FakeOut()
    assert tool_names_from_run_output(o) == ["retrieve_ordered_context", "search_client_memory"]


def test_log_agent_run_obs_line_format() -> None:
    line = log_agent_run_obs(
        request_id="r1",
        session_id="s1",
        out=_FakeOut(),
        elapsed_s=0.042,
        route="/chat",
    )
    assert grep_obs_line_pattern().match(line)


def test_log_agent_run_empty_tools() -> None:
    o = _FakeOut()
    o.tools = []
    line = log_agent_run_obs(request_id="a", session_id="b", out=o, elapsed_s=1.0, route="/chat")
    assert "tools=-" in line


def test_log_agent_run_uses_metrics_details_when_top_level_zero() -> None:
    """与 Agno 一致：部分运行只在 ``metrics.details`` 中给出各模型 token。"""
    from agno.metrics import ModelMetrics, RunMetrics

    mm = ModelMetrics(
        id="gpt-4o-mini", provider="openai", input_tokens=3, output_tokens=2, total_tokens=5
    )
    m = RunMetrics(input_tokens=0, output_tokens=0, total_tokens=0, details={"model": [mm]})
    o = _FakeOut()
    o.metrics = m
    line = log_agent_run_obs(request_id="a", session_id="b", out=o, elapsed_s=0.1, route="/chat")
    assert "tok_in=3" in line and "tok_out=2" in line and "tok_total=5" in line
