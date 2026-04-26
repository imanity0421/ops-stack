from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_os.knowledge.group_id import graphiti_group_id, system_graphiti_group_id
from agent_os.memory.models import CLIENT_SHARED_USER_ID
from agent_os.memory.migration_v2 import migrate_knowledge_jsonl_v2, migrate_local_memory_v2


def test_migrate_local_memory_v2_merges_legacy_root_bucket(tmp_path: Path) -> None:
    p = tmp_path / "local_memory.json"
    p.write_text(
        json.dumps(
            {
                "users": {
                    "c1": {
                        "memories": [
                            {"text": "shared A", "metadata": {"k": 1}},
                            {"text": "dup", "metadata": {}},
                        ]
                    },
                    f"c1::{CLIENT_SHARED_USER_ID}": {
                        "memories": [
                            {"text": "dup", "metadata": {}},
                            {"text": "already", "metadata": {}},
                        ]
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    r = migrate_local_memory_v2(p, dry_run=False)
    assert r["status"] == "ok"
    data = json.loads(p.read_text(encoding="utf-8"))
    users = data["users"]
    assert "c1" not in users
    key = f"c1::{CLIENT_SHARED_USER_ID}"
    assert key in users
    mems = users[key]["memories"]
    texts = {m["text"] for m in mems}
    assert texts == {"shared A", "dup", "already"}


def test_migrate_local_memory_v2_dry_run_no_write(tmp_path: Path) -> None:
    p = tmp_path / "local_memory.json"
    original = json.dumps({"users": {"c1": {"memories": [{"text": "x", "metadata": {}}]}}})
    p.write_text(original, encoding="utf-8")
    r = migrate_local_memory_v2(p, dry_run=True)
    assert r["status"] == "dry_run"
    assert p.read_text(encoding="utf-8") == original


def test_migrate_local_memory_v2_bad_utf8_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "local_memory.json"
    p.write_bytes(b"\xff\xfe\x00")

    r = migrate_local_memory_v2(p)

    assert r["status"] == "error"
    assert r["reason"] == "invalid_json"


def test_migrate_knowledge_jsonl_v2_duplicate(tmp_path: Path) -> None:
    legacy_gid = graphiti_group_id("demo", "my_skill")
    new_gid = system_graphiti_group_id("my_skill")
    assert legacy_gid != new_gid

    p = tmp_path / "k.jsonl"
    row = {"group_id": legacy_gid, "text": "hello", "meta": 1}
    p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    r = migrate_knowledge_jsonl_v2(p, dry_run=False, mode="duplicate")
    assert r["status"] == "ok"
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0]["group_id"] == legacy_gid
    assert lines[1]["group_id"] == new_gid
    assert lines[1]["text"] == "hello"


def test_migrate_knowledge_jsonl_v2_bad_utf8_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_bytes(b"\xff\xfe\x00")

    r = migrate_knowledge_jsonl_v2(p)

    assert r["status"] == "error"
    assert r["reason"] == "invalid_jsonl"


def test_migrate_knowledge_jsonl_v2_replace(tmp_path: Path) -> None:
    legacy_gid = graphiti_group_id("demo", "s")
    p = tmp_path / "k.jsonl"
    p.write_text(json.dumps({"group_id": legacy_gid, "text": "t"}, ensure_ascii=False) + "\n")
    migrate_knowledge_jsonl_v2(p, dry_run=False, mode="replace")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["group_id"] == system_graphiti_group_id("s")


def test_migrate_knowledge_jsonl_v2_invalid_mode() -> None:
    with pytest.raises(ValueError):
        migrate_knowledge_jsonl_v2(Path("nope"), dry_run=True, mode="bad")
