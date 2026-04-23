from __future__ import annotations

from ops_agent.mcp.fixture_probe import format_probe_for_agent, load_probe_data


def test_load_default_probe() -> None:
    data = load_probe_data(None)
    assert "market_snapshot" in data or "error" not in data
    assert data.get("probe_version") == "1.0"


def test_format_probe() -> None:
    s = format_probe_for_agent({"probe_version": "1", "market_snapshot": {"platform": "x", "category": "y"}})
    assert "x" in s and "y" in s
