from __future__ import annotations

import builtins
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_os import cli
from agent_os.agent.task_memory import TaskMemoryStore, TaskSummary
from agent_os.knowledge import asset_ingest as asset_ingest_mod
from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.models import MemoryLane, UserFact


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


def test_cli_interactive_skips_blank_and_handles_unusual_characters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_OS_ENABLE_TASK_MEMORY", "0")
    monkeypatch.setenv("AGENT_OS_ENABLE_SESSION_DB", "0")
    monkeypatch.setenv("AGENT_OS_ENABLE_CONTEXT_BUILDER", "0")
    monkeypatch.setenv("AGENT_OS_LOCAL_MEMORY_PATH", str(tmp_path / "local.json"))
    monkeypatch.setenv("AGENT_OS_HISTORICAL_PATH", str(tmp_path / "hindsight.jsonl"))

    seen_messages: list[str] = []

    class FakeAgent:
        def run(self, message: str, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            seen_messages.append(message)
            return SimpleNamespace(content="ok")

    answers = iter(["   ", "\x00异常字符\u200b方案 <xml> & text", "exit"])
    monkeypatch.setattr(cli, "get_agent", lambda *_args, **_kwargs: FakeAgent())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))

    rc = cli.main(["--client-id", "c1", "--no-knowledge", "--no-async-review"])

    assert rc == 0
    assert seen_messages == ["\x00异常字符\u200b方案 <xml> & text"]


def test_cli_context_builder_fetches_history_with_effective_summary_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_db = tmp_path / "task.db"
    store = TaskMemoryStore(task_db)
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="既有任务",
    )
    store.upsert_summary(
        TaskSummary(
            session_id="s1",
            task_id=task.task_id,
            summary_text="- 当前任务目标：验证 CLI history cap",
            summary_version=1,
            covered_message_count=6,
            updated_at="2026-04-27T00:00:00+00:00",
        )
    )
    monkeypatch.setenv("AGENT_OS_ENABLE_TASK_MEMORY", "1")
    monkeypatch.setenv("AGENT_OS_TASK_MEMORY_DB_PATH", str(task_db))
    monkeypatch.setenv("AGENT_OS_ENABLE_SESSION_DB", "0")
    monkeypatch.setenv("AGENT_OS_CONTEXT_AUTO_RETRIEVE", "0")
    monkeypatch.setenv("AGENT_OS_SESSION_HISTORY_MAX_MESSAGES", "8")
    monkeypatch.setenv("AGENT_OS_SESSION_HISTORY_CAP_WHEN_TASK_SUMMARY", "2")
    monkeypatch.setenv("AGENT_OS_LOCAL_MEMORY_PATH", str(tmp_path / "local.json"))
    monkeypatch.setenv("AGENT_OS_HISTORICAL_PATH", str(tmp_path / "hindsight.jsonl"))

    captured_limits: list[int] = []

    class FakeAgent:
        db = object()

        def get_session_messages(self, **kwargs: Any) -> list[Any]:
            captured_limits.append(kwargs["limit"])
            return []

        def run(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(content="ok")

    monkeypatch.setattr(cli, "get_agent", lambda *_args, **_kwargs: FakeAgent())
    answers = iter(["继续", "exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))

    rc = cli.main(
        ["--client-id", "c1", "--session-id", "s1", "--no-knowledge", "--no-async-review"]
    )

    assert rc == 0
    assert captured_limits == [2]


def test_cli_context_diagnose_outputs_json(tmp_path: Path, monkeypatch, capsys) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps([{"role": "user", "content": "上一轮问题"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_CONTEXT_ESTIMATE_TOKENS", "0")
    monkeypatch.setenv("AGENT_OS_CONTEXT_MAX_CHARS", "1000")

    rc = cli.main(
        [
            "context-diagnose",
            "--message",
            "继续给我方案",
            "--client-id",
            "c1",
            "--history-json",
            str(history),
            "--json",
        ]
    )

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_chars"] > 0
    assert data["max_total_chars"] == 1000
    assert any(b["name"] == "recent_history" for b in data["blocks"])
    assert any(b["name"] == "current_user_message" for b in data["blocks"])


def test_cli_context_diagnose_can_fail_on_budget(monkeypatch, capsys) -> None:
    monkeypatch.setenv("AGENT_OS_CONTEXT_ESTIMATE_TOKENS", "0")
    monkeypatch.setenv("AGENT_OS_CONTEXT_MAX_CHARS", "240")

    rc = cli.main(
        [
            "context-diagnose",
            "--message",
            "请处理以下超长材料：" + ("材料片段 " * 80),
            "--client-id",
            "c1",
            "--json",
            "--fail-on-budget",
            "over_budget",
        ]
    )

    assert rc == 2
    data = json.loads(capsys.readouterr().out)
    assert data["budget_guard"]["is_at_blocking_limit"] is True


def test_cli_context_diagnose_smoke_self_heals_over_budget(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    history = tmp_path / "history.json"
    retrieved = tmp_path / "retrieved.txt"
    history.write_text(
        json.dumps(
            [
                {"role": "user", "content": "上一轮问题 " * 80},
                {"role": "assistant", "content": "上一轮回复 " * 120},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    retrieved.write_text(
        "<ordered_context>" + ("召回证据 " * 140) + "</ordered_context>",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_CONTEXT_ESTIMATE_TOKENS", "0")
    monkeypatch.setenv("AGENT_OS_CONTEXT_MAX_CHARS", "1200")
    monkeypatch.setenv("AGENT_OS_CONTEXT_SELF_HEAL_OVER_BUDGET", "1")

    rc = cli.main(
        [
            "context-diagnose",
            "--message",
            "请继续推进方案",
            "--client-id",
            "c1",
            "--history-json",
            str(history),
            "--retrieved-context-file",
            str(retrieved),
            "--json",
        ]
    )

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    signal_names = {s["name"] for s in data["signals"]}
    assert data["total_chars"] <= 1200
    assert data["budget_status"] != "over_budget"
    assert "budget_self_heal" in signal_names
    assert "hard_budget_trim" in signal_names


def test_cli_context_diagnose_smoke_reports_tool_history_budget(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            [
                {"role": "tool", "content": "旧工具输出 " * 30},
                {"role": "tool", "content": "新工具输出 " * 10},
                {"role": "assistant", "content": "继续处理"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_CONTEXT_ESTIMATE_TOKENS", "0")
    monkeypatch.setenv("AGENT_OS_CONTEXT_MAX_CHARS", "5000")
    monkeypatch.setenv("AGENT_OS_CONTEXT_TOOL_OUTPUT_MAX_CHARS", "500")
    monkeypatch.setenv("AGENT_OS_CONTEXT_TOOL_OUTPUTS_TOTAL_MAX_CHARS", "80")

    rc = cli.main(
        [
            "context-diagnose",
            "--message",
            "继续",
            "--client-id",
            "c1",
            "--history-json",
            str(history),
            "--json",
        ]
    )

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    recent = next(b for b in data["blocks"] if b["name"] == "recent_history")
    assert "tool_total_budget=80" in recent["note"]
    assert "tool_omitted=1" in recent["note"]


def test_cli_hindsight_index_status_rebuild_invalidate(tmp_path: Path, capsys) -> None:
    hindsight = tmp_path / "hindsight.jsonl"
    store = HindsightStore(hindsight)
    store.append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="运维教训：交付前必须确认关键约束。",
            fact_type="feedback",
        )
    )

    assert cli.main(["hindsight-index", "rebuild", "--path", str(hindsight)]) == 0
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["status"] == "ok"
    assert rebuilt["row_count"] == 1

    assert cli.main(["hindsight-index", "status", "--path", str(hindsight)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["fresh"] is True

    assert cli.main(["hindsight-index", "invalidate", "--path", str(hindsight)]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed == {"status": "ok", "removed": True}


def test_cli_hindsight_vector_index_ops(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_os.memory.hindsight_vector._embed_text_openai",
        lambda text, *, cfg: [1.0, 0.0],
    )
    hindsight = tmp_path / "hindsight.jsonl"
    vector_path = tmp_path / "hindsight_vector.lancedb"
    HindsightStore(hindsight).append_feedback(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="运维教训：发布前必须先跑回归测试。",
            fact_type="feedback",
        )
    )

    assert (
        cli.main(
            [
                "hindsight-index",
                "vector-rebuild",
                "--path",
                str(hindsight),
                "--vector-path",
                str(vector_path),
            ]
        )
        == 0
    )
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["status"] == "ok"
    assert rebuilt["row_count"] == 1

    assert (
        cli.main(
            [
                "hindsight-index",
                "vector-status",
                "--path",
                str(hindsight),
                "--vector-path",
                str(vector_path),
            ]
        )
        == 0
    )
    status = json.loads(capsys.readouterr().out)
    assert status["enabled"] is True
    assert status["fresh"] is True


def test_cli_hindsight_vector_rebuild_error_returns_nonzero(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class _Store:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        def rebuild_vector_index(self):
            return {"status": "error", "reason": "delete_before_rebuild_failed"}

    monkeypatch.setattr(cli, "HindsightStore", _Store)

    rc = cli.main(
        [
            "hindsight-index",
            "vector-rebuild",
            "--path",
            str(tmp_path / "hindsight.jsonl"),
        ]
    )

    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["status"] == "error"


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
