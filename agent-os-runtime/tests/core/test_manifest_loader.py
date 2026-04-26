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


def test_load_manifest_accepts_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text("\ufeff" + json.dumps({"system_prompt": "ok"}), encoding="utf-8")

    assert load_agent_manifest(p) is not None


def test_load_manifest_validation_error_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps({"enabled_tools": "not-a-list"}),
        encoding="utf-8",
    )
    assert load_agent_manifest(p) is None


def test_load_manifest_bad_utf8_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_bytes(b"\xff\xfe\x00")

    assert load_agent_manifest(p) is None


def test_registry_skips_invalid_overlay_manifest(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text(
        json.dumps({"enabled_tools": "not-a-list"}),
        encoding="utf-8",
    )
    (tmp_path / "good.json").write_text(
        json.dumps({"system_prompt": "ok", "enabled_tools": ["fetch_probe_context"]}),
        encoding="utf-8",
    )
    reg = load_skill_manifest_registry(tmp_path)
    assert "bad" not in reg
    assert "good" in reg


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
