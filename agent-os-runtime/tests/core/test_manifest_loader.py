from __future__ import annotations

import json
from pathlib import Path

from agent_os.agent.tools import filter_tools_by_manifest
from agent_os.manifest_loader import (
    enabled_tool_name_set,
    load_agent_manifest,
    load_skill_manifest_registry,
    resolve_effective_skill_id,
)


def test_load_manifest(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps(
            {
                "manifest_version": "1.0",
                "handbook_version": "9.9.9",
                "system_prompt": "测试配方",
                "model": "gpt-4o-mini",
                "enabled_tools": ["retrieve_ordered_context", "fetch_probe_context"],
            }
        ),
        encoding="utf-8",
    )
    m = load_agent_manifest(p)
    assert m is not None
    assert m.handbook_version == "9.9.9"
    assert enabled_tool_name_set(m) == {"retrieve_ordered_context", "fetch_probe_context"}


def test_skill_registry_has_builtin_default_agent() -> None:
    reg = load_skill_manifest_registry(None)
    assert "default_agent" in reg
    eff = resolve_effective_skill_id(None, "default_agent", reg)
    assert eff == "default_agent"


def test_filter_tools_empty_enabled_returns_all() -> None:
    class Fn:
        name = "a"

    tools = [Fn()]  # type: ignore[list-item]
    assert len(filter_tools_by_manifest(tools, None)) == 1
