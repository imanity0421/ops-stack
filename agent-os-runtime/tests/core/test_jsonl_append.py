from __future__ import annotations

import json
from pathlib import Path

from agent_os.knowledge.group_id import system_graphiti_group_id
from agent_os.knowledge.jsonl_append import append_knowledge_lines


def test_append_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "k.jsonl"
    n = append_knowledge_lines(out, "demo_client", ["hello", "world"])
    assert n == 2
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert row0["text"] == "hello"
    assert row0["group_id"] == system_graphiti_group_id("default_agent")


def test_append_jsonl_skips_blank_texts(tmp_path: Path) -> None:
    out = tmp_path / "k.jsonl"
    n = append_knowledge_lines(out, "demo_client", ["", "  \n  ", "hello"])

    assert n == 1
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"group_id": system_graphiti_group_id("default_agent"), "text": "hello"}]
