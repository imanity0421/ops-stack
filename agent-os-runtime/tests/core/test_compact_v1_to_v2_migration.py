"""Tests for ``scripts/migrate_compact_v1_to_v2.py``.

Phase 9: CompactSummary schema collapsed from three layers to two
(business_writing_pack removed). The migration script rewrites the SQLite
``compact_summaries`` table in place.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "migrate_compact_v1_to_v2.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migrate_compact_v1_to_v2", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE compact_summaries (
            session_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            summary_version INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            covered_message_start_id TEXT,
            covered_message_end_id TEXT,
            covered_message_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            compact_model TEXT NOT NULL,
            compact_policy_version TEXT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(session_id, task_id)
        );
        """
    )
    conn.commit()
    conn.close()


def _insert(
    path: Path,
    *,
    session_id: str,
    task_id: str,
    summary: dict,
    schema_version: str,
) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO compact_summaries (session_id, task_id, summary_version, summary_json, "
        "schema_version, covered_message_count, updated_at, compact_model, "
        "compact_policy_version, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            task_id,
            1,
            json.dumps(summary, ensure_ascii=False),
            schema_version,
            0,
            "2026-04-30T00:00:00Z",
            "fallback",
            "compact_summary_v1",
            "active",
        ),
    )
    conn.commit()
    conn.close()


def _read(path: Path, *, session_id: str, task_id: str) -> tuple[dict, str]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT summary_json, schema_version FROM compact_summaries "
        "WHERE session_id = ? AND task_id = ?",
        (session_id, task_id),
    ).fetchone()
    conn.close()
    assert row is not None
    return json.loads(row["summary_json"]), row["schema_version"]


def test_migrate_drops_business_writing_pack_and_bumps_version(tmp_path: Path) -> None:
    db = tmp_path / "task.db"
    _make_db(db)
    _insert(
        db,
        session_id="s1",
        task_id="t1",
        summary={
            "schema_version": "v1",
            "core": {"goal": "g", "current_artifact_refs": ["a1"]},
            "business_writing_pack": {"brand_voice": "playful"},
            "skill_state": None,
        },
        schema_version="v1",
    )

    module = _load_migration_module()
    stats = module.migrate(db, dry_run=False)

    assert stats == {"scanned": 1, "migrated": 1, "skipped_v2": 0, "errors": 0}
    payload, ver = _read(db, session_id="s1", task_id="t1")
    assert ver == "v2"
    assert payload["schema_version"] == "v2"
    assert "business_writing_pack" not in payload
    assert payload["core"]["goal"] == "g"


def test_migrate_skips_already_v2_rows(tmp_path: Path) -> None:
    db = tmp_path / "task.db"
    _make_db(db)
    _insert(
        db,
        session_id="s1",
        task_id="t1",
        summary={
            "schema_version": "v2",
            "core": {"goal": "g"},
            "skill_state": None,
        },
        schema_version="v2",
    )

    module = _load_migration_module()
    stats = module.migrate(db, dry_run=False)

    assert stats["scanned"] == 1
    assert stats["migrated"] == 0
    assert stats["skipped_v2"] == 1


def test_migrate_dry_run_does_not_mutate(tmp_path: Path) -> None:
    db = tmp_path / "task.db"
    _make_db(db)
    summary = {
        "schema_version": "v1",
        "core": {"goal": "g"},
        "business_writing_pack": {"brand_voice": "playful"},
        "skill_state": None,
    }
    _insert(
        db,
        session_id="s1",
        task_id="t1",
        summary=summary,
        schema_version="v1",
    )

    module = _load_migration_module()
    stats = module.migrate(db, dry_run=True)

    assert stats == {"scanned": 1, "migrated": 1, "skipped_v2": 0, "errors": 0}
    payload, ver = _read(db, session_id="s1", task_id="t1")
    assert ver == "v1"
    assert payload["schema_version"] == "v1"
    assert payload["business_writing_pack"] == {"brand_voice": "playful"}


def test_migrate_treats_missing_version_as_v1(tmp_path: Path) -> None:
    db = tmp_path / "task.db"
    _make_db(db)
    _insert(
        db,
        session_id="s1",
        task_id="t1",
        summary={"core": {"goal": "g"}, "business_writing_pack": {"a": 1}},
        schema_version="",
    )

    module = _load_migration_module()
    stats = module.migrate(db, dry_run=False)

    assert stats["migrated"] == 1
    payload, ver = _read(db, session_id="s1", task_id="t1")
    assert ver == "v2"
    assert payload["schema_version"] == "v2"
    assert "business_writing_pack" not in payload


def test_compact_summary_from_json_v1_inline_migration() -> None:
    """Runtime fallback path: compact_summary_from_json should also migrate v1 to v2."""
    from agent_os.agent.compact import compact_summary_from_json

    raw = json.dumps(
        {
            "schema_version": "v1",
            "core": {
                "goal": "test",
                "current_artifact_refs": [],
                "pinned_refs": [],
                "constraints": [],
                "progress": [],
                "pending": [],
                "last_user_instruction": "",
                "open_questions": [],
            },
            "business_writing_pack": {"brand_voice": "playful"},
            "skill_state": None,
        }
    )

    summary = compact_summary_from_json(raw)
    assert summary.schema_version == "v2"
    assert summary.skill_state is None
    assert not hasattr(summary, "business_writing_pack")
