from __future__ import annotations

from pathlib import Path

from agent_os.mcp.fixture_probe import format_probe_for_agent, load_probe_data


def test_load_default_probe() -> None:
    data = load_probe_data(None)
    assert "market_snapshot" in data or "error" not in data
    assert data.get("probe_version") == "1.0"


def test_format_probe() -> None:
    s = format_probe_for_agent(
        {"probe_version": "1", "market_snapshot": {"platform": "x", "category": "y"}}
    )
    assert "x" in s and "y" in s


def test_format_probe_ignores_non_object_snapshot() -> None:
    s = format_probe_for_agent({"probe_version": "1", "market_snapshot": "bad"})
    assert "版本: 1" in s
    assert "平台:" in s


def test_invalid_probe_fixture_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not-json", encoding="utf-8")

    data = load_probe_data(p)

    assert data["error"] == "invalid_probe_fixture"


def test_bad_utf8_probe_fixture_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_bytes(b"\xff\xfe\x00")

    data = load_probe_data(p)

    assert data["error"] == "invalid_probe_fixture"
