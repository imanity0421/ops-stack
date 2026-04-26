from __future__ import annotations

import builtins
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_os import cli
from agent_os.knowledge import asset_ingest as asset_ingest_mod


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


def test_cli_graphiti_dry_run_rejects_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "episodes.json"
    p.write_text("{bad", encoding="utf-8")

    assert cli.main(["graphiti-ingest", str(p), "--dry-run"]) == 1


def test_cli_graphiti_rejects_missing_input_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"

    assert cli.main(["graphiti-ingest", str(p), "--dry-run"]) == 1


def test_cli_asset_ingest_rejects_missing_input_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.txt"

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 1


def test_cli_asset_ingest_rejects_bad_utf8_text_file(tmp_path: Path) -> None:
    p = tmp_path / "bad.txt"
    p.write_bytes(b"\xff\xfe\x00")

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 1


def test_cli_asset_ingest_rejects_bad_utf8_jsonl_file(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_bytes(b"\xff\xfe\x00")

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 1


def test_cli_asset_ingest_returns_nonzero_when_text_rejected(tmp_path: Path) -> None:
    p = tmp_path / "short.txt"
    p.write_text("too short", encoding="utf-8")

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 1


def test_cli_asset_ingest_returns_nonzero_when_jsonl_accepts_none(
    tmp_path: Path,
) -> None:
    p = tmp_path / "assets.jsonl"
    p.write_text('{"text":"too short"}\n', encoding="utf-8")

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 1


def test_cli_asset_ingest_jsonl_quarantined_counts_as_success(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "assets.jsonl"
    p.write_text('{"text":"valid but quarantined"}\n', encoding="utf-8")
    monkeypatch.setattr(cli, "asset_store_from_settings", lambda *args, **kwargs: object())

    def fake_ingest_jsonl(*args, **kwargs) -> dict[str, Any]:
        _ = (args, kwargs)
        return {
            "total": 1,
            "accepted": 0,
            "quarantined": 1,
            "rejected": 0,
            "duplicate_skipped": 0,
            "reasons": {},
        }

    monkeypatch.setattr(asset_ingest_mod, "ingest_jsonl", fake_ingest_jsonl)

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 0


def test_cli_asset_ingest_jsonl_duplicate_counts_as_success(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "assets.jsonl"
    p.write_text('{"text":"already exists"}\n', encoding="utf-8")
    monkeypatch.setattr(cli, "asset_store_from_settings", lambda *args, **kwargs: object())

    def fake_ingest_jsonl(*args, **kwargs) -> dict[str, Any]:
        _ = (args, kwargs)
        return {
            "total": 1,
            "accepted": 0,
            "quarantined": 0,
            "rejected": 0,
            "duplicate_skipped": 1,
            "reasons": {"duplicate_skip": 1},
        }

    monkeypatch.setattr(asset_ingest_mod, "ingest_jsonl", fake_ingest_jsonl)

    assert cli.main(["asset-ingest", str(p), "--client-id", "c1", "--no-llm"]) == 0


def test_cli_eval_accepts_utf8_bom_json(tmp_path: Path) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        "\ufeff"
        + json.dumps(
            {
                "name": "bom",
                "assistant_turns": ["hello"],
                "golden_rules": [{"pattern": "never-match", "message": "should not hit"}],
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["eval", str(p)]) == 0


def test_cli_eval_missing_case_file_returns_nonzero(tmp_path: Path) -> None:
    p = tmp_path / "missing_case.json"

    assert cli.main(["eval", str(p)]) == 1


def test_cli_graphiti_entitlements_set_and_show(tmp_path: Path, capsys) -> None:
    p = tmp_path / "entitlements.json"
    rc = cli.main(
        [
            "graphiti-entitlements",
            "--path",
            str(p),
            "--set-global",
            "s1,s2",
            "--client-id",
            "c1",
            "--set-client",
            "s2",
            "--show",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"global_allowed_skill_ids"' in out
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["global_allowed_skill_ids"] == ["s1", "s2"]
    assert data["client_entitlements"]["c1"] == ["s2"]
    assert data["revision"] >= 1


def test_cli_graphiti_entitlements_remove_client_requires_client_id() -> None:
    rc = cli.main(["graphiti-entitlements", "--remove-client"])
    assert rc == 1


def test_cli_graphiti_entitlements_writes_audit_log(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "entitlements.json"
    audit = tmp_path / "ent_audit.jsonl"
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH", str(audit))
    monkeypatch.setenv("AGENT_OS_ACTOR", "tester_cli")
    rc = cli.main(
        [
            "graphiti-entitlements",
            "--path",
            str(p),
            "--set-global",
            "s1",
        ]
    )
    assert rc == 0
    rows = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows
    assert rows[-1]["actor"] == "tester_cli"
    assert rows[-1]["action"] == "set_global"


def test_cli_graphiti_entitlements_conflict_returns_2(tmp_path: Path) -> None:
    p = tmp_path / "entitlements.json"
    assert cli.main(["graphiti-entitlements", "--path", str(p), "--set-global", "s1"]) == 0
    rc = cli.main(
        [
            "graphiti-entitlements",
            "--path",
            str(p),
            "--set-global",
            "s2",
            "--expected-revision",
            "0",
        ]
    )
    assert rc == 2


def test_cli_graphiti_entitlements_mutates_latest_file_without_stale_overwrite(
    tmp_path: Path, monkeypatch
) -> None:
    p = tmp_path / "entitlements.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "revision": 0,
                "global_allowed_skill_ids": [],
                "client_entitlements": {"c2": ["s2"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def stale_load(_path: Path) -> dict[str, object]:
        return {
            "version": 1,
            "revision": 0,
            "global_allowed_skill_ids": [],
            "client_entitlements": {},
        }

    monkeypatch.setattr(cli, "load_entitlements_file", stale_load)

    rc = cli.main(
        [
            "graphiti-entitlements",
            "--path",
            str(p),
            "--client-id",
            "c1",
            "--set-client",
            "s1",
        ]
    )

    assert rc == 0
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["client_entitlements"] == {"c1": ["s1"], "c2": ["s2"]}
