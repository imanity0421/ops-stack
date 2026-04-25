from __future__ import annotations

import json
from pathlib import Path

from agent_os.handoff import load_handoff_instruction_lines


def test_handoff_missing_returns_empty(tmp_path: Path) -> None:
    assert load_handoff_instruction_lines(tmp_path / "nope.json") == []


def test_handoff_valid_summary(tmp_path: Path) -> None:
    p = tmp_path / "handbook_handoff.json"
    p.write_text(
        json.dumps(
            {
                "handoff_version": "1.0",
                "created_utc": "2026-01-01T00:00:00+00:00",
                "video_raw_ingest_schema_ref": "/x/schema.json",
                "lessons": [
                    {"relpath": "a/x.json", "sha256": "0" * 64, "valid": True, "errors": []},
                    {"relpath": "b/y.json", "sha256": "1" * 64, "valid": False, "errors": ["e"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    lines = load_handoff_instruction_lines(p)
    assert len(lines) == 3
    assert "条目数=2" in lines[0]
    assert "校验通过=1" in lines[0]
    assert "未通过=1" in lines[0]
