"""Skill 包白名单动态加载（P0-1 / docs/AGENT_OS_ROADMAP）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ops_agent.agent.factory import get_agent
from ops_agent.agent.skills import SkillPackageLoadError, get_incremental_tools
from ops_agent.config import Settings
from ops_agent.memory.controller import MemoryController


def _tool_names(tools: list[object]) -> set[str]:
    return {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}


def test_allowlist_empty_no_incremental_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AGENT_LOADABLE_SKILL_PACKAGES", "")
    s = Settings.from_env()
    assert s.skill_packages_allowlist == frozenset()
    assert get_incremental_tools("toy_skill", settings=s) == []


def test_allowlist_toy_skill_loads_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AGENT_LOADABLE_SKILL_PACKAGES", "toy_skill")
    s = Settings.from_env()
    tools = get_incremental_tools("toy_skill", settings=s)
    assert "ping_toy_skill" in _tool_names(tools)


def test_not_in_allowlist_never_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AGENT_LOADABLE_SKILL_PACKAGES", "toy_skill")
    s = Settings.from_env()
    assert get_incremental_tools("default_ops", settings=s) == []


def test_allowlisted_missing_subpackage_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPS_AGENT_LOADABLE_SKILL_PACKAGES", "definitely_missing_pkg_42")
    s = Settings.from_env()
    with pytest.raises(SkillPackageLoadError) as e:
        get_incremental_tools("definitely_missing_pkg_42", settings=s)
    assert "未找到" in str(e.value) or "definitely" in str(e.value).lower()


def test_settings_invalid_allow_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AGENT_LOADABLE_SKILL_PACKAGES", "a..b")
    with pytest.raises(ValueError, match="非法"):
        Settings.from_env()


def test_get_agent_toy_includes_ping_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AGENT_LOADABLE_SKILL_PACKAGES", "toy_skill")
    local = tmp_path / "m.json"
    hind = tmp_path / "h.jsonl"
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    s = Settings.from_env()
    ag = get_agent(
        ctrl,
        client_id="c1",
        user_id="u1",
        knowledge=None,
        settings=s,
        skill_id="toy_skill",
    )
    tlist = ag.tools
    if tlist is None:
        tlist = []
    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tlist}
    assert "ping_toy_skill" in names
    assert "retrieve_ordered_context" in names
