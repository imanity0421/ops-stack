from __future__ import annotations

import json
from pathlib import Path

from ops_agent.knowledge.jsonl_append import append_knowledge_lines


def test_append_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "k.jsonl"
    n = append_knowledge_lines(out, "demo_client", ["hello", "world"])
    assert n == 2
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert row0["text"] == "hello"
    assert "group_id" in row0
