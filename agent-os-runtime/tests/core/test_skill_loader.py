"""Skill 包白名单动态加载（P0-1 / docs/AGENT_OS_ROADMAP）。"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from agent_os.agent.factory import get_agent
from agent_os.agent.skills import SkillPackageLoadError, get_incremental_tools
from agent_os.config import Settings
from agent_os.memory.controller import MemoryController


def _tool_names(tools: list[object]) -> set[str]:
    return {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}


def _install_sample_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("agent_os.agent.skills.sample_skill")

    def ping_sample_skill() -> str:
        return "pong:sample_skill"

    def get_tools() -> list[object]:
        return [ping_sample_skill]

    mod.get_tools = get_tools  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_os.agent.skills.sample_skill", mod)


def test_allowlist_empty_no_incremental_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "")
    s = Settings.from_env()
    assert s.skill_packages_allowlist == frozenset()
    assert get_incremental_tools("sample_skill", settings=s) == []


def test_allowlist_sample_skill_loads_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sample_skill(monkeypatch)
    monkeypatch.setenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "sample_skill")
    s = Settings.from_env()
    tools = get_incremental_tools("sample_skill", settings=s)
    assert "ping_sample_skill" in _tool_names(tools)


def test_not_in_allowlist_never_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "sample_skill")
    s = Settings.from_env()
    assert get_incremental_tools("default_agent", settings=s) == []


def test_allowlisted_missing_subpackage_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "definitely_missing_pkg_42")
    s = Settings.from_env()
    with pytest.raises(SkillPackageLoadError) as e:
        get_incremental_tools("definitely_missing_pkg_42", settings=s)
    assert "未找到" in str(e.value) or "definitely" in str(e.value).lower()


def test_settings_invalid_allow_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "a..b")
    with pytest.raises(ValueError, match="非法"):
        Settings.from_env()


def test_get_agent_sample_includes_ping_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_sample_skill(monkeypatch)
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    (manifest_dir / "sample_skill.json").write_text(
        json.dumps(
            {
                "manifest_version": "1.0",
                "handbook_version": "0.1.0",
                "system_prompt": "示例 skill，仅用于测试外部 manifest 与工具包热插拔。",
                "model": "gpt-4o-mini",
                "agent_name": "SampleSkill",
                "enabled_tools": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "sample_skill")
    monkeypatch.setenv("AGENT_OS_MANIFEST_DIR", str(manifest_dir))
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
        skill_id="sample_skill",
    )
    tlist = ag.tools
    if tlist is None:
        tlist = []
    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tlist}
    assert "ping_sample_skill" in names
    assert "retrieve_ordered_context" in names
