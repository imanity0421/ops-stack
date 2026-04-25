from __future__ import annotations

import builtins
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_os import cli


def test_cli_task_memory_records_turn_and_injects_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_db = tmp_path / "task.db"
    monkeypatch.setenv("AGENT_OS_ENABLE_TASK_MEMORY", "1")
    monkeypatch.setenv("AGENT_OS_TASK_MEMORY_DB_PATH", str(task_db))
    monkeypatch.setenv("AGENT_OS_ENABLE_SESSION_DB", "0")
    monkeypatch.setenv("AGENT_OS_LOCAL_MEMORY_PATH", str(tmp_path / "local.json"))
    monkeypatch.setenv("AGENT_OS_HISTORICAL_PATH", str(tmp_path / "hindsight.jsonl"))

    calls: list[dict[str, Any]] = []

    class FakeAgent:
        def run(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(content="ok")

    def fake_get_agent(*_args: Any, **kwargs: Any) -> FakeAgent:
        calls.append(kwargs)
        return FakeAgent()

    answers = iter(["帮我做一个通用方案", "exit"])
    monkeypatch.setattr(cli, "get_agent", fake_get_agent)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))

    rc = cli.main(["--client-id", "c1", "--no-knowledge", "--no-async-review"])

    assert rc == 0
    assert calls
    assert calls[0]["session_task_index"]

    with sqlite3.connect(str(task_db)) as conn:
        rows = conn.execute(
            "SELECT role, content FROM session_messages ORDER BY sequence_no"
        ).fetchall()
    assert rows == [("user", "帮我做一个通用方案"), ("assistant", "ok")]


def test_cli_task_memory_records_effective_skill_for_unknown_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_db = tmp_path / "task.db"
    monkeypatch.setenv("AGENT_OS_ENABLE_TASK_MEMORY", "1")
    monkeypatch.setenv("AGENT_OS_TASK_MEMORY_DB_PATH", str(task_db))
    monkeypatch.setenv("AGENT_OS_ENABLE_SESSION_DB", "0")
    monkeypatch.setenv("AGENT_OS_LOCAL_MEMORY_PATH", str(tmp_path / "local.json"))
    monkeypatch.setenv("AGENT_OS_HISTORICAL_PATH", str(tmp_path / "hindsight.jsonl"))

    class FakeAgent:
        def run(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(content="ok")

    answers = iter(["帮我做一个通用方案", "exit"])
    monkeypatch.setattr(cli, "get_agent", lambda *_args, **_kwargs: FakeAgent())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))

    rc = cli.main(
        ["--client-id", "c1", "--skill", "missing_skill", "--no-knowledge", "--no-async-review"]
    )

    assert rc == 0
    with sqlite3.connect(str(task_db)) as conn:
        skill = conn.execute("SELECT primary_skill_id FROM task_segments").fetchone()[0]
    assert skill == "default_agent"


def test_cli_graphiti_dry_run_rejects_non_list_episodes(tmp_path: Path) -> None:
    p = tmp_path / "episodes.json"
    p.write_text(json.dumps({"episodes": None}), encoding="utf-8")

    assert cli.main(["graphiti-ingest", str(p), "--dry-run"]) == 1


def test_cli_graphiti_dry_run_accepts_utf8_bom_json(tmp_path: Path) -> None:
    p = tmp_path / "episodes.json"
    p.write_text("\ufeff" + json.dumps({"episodes": []}), encoding="utf-8")

    assert cli.main(["graphiti-ingest", str(p), "--dry-run"]) == 0


def test_cli_eval_accepts_utf8_bom_json(tmp_path: Path) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        "\ufeff"
        + json.dumps(
            {
                "name": "bom",
                "assistant_turns": ["hello"],
                "golden_rules": [],
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["eval", str(p)]) == 0
