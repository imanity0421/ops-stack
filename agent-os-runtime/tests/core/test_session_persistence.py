from __future__ import annotations

from pathlib import Path

import pytest

from agent_os.agent.session_db import create_session_db, session_db_summary
from agent_os.config import Settings
from agent_os.memory.controller import MemoryController


def test_create_session_db_disabled_returns_none() -> None:
    s = Settings(
        enable_session_db=False,
        session_sqlite_path=Path("data/x.db"),
    )
    assert create_session_db(s) is None


def test_create_session_db_uses_sqlite_file(tmp_path: Path) -> None:
    dbfile = tmp_path / "sub" / "s.db"
    s = Settings(
        enable_session_db=True,
        session_sqlite_path=dbfile,
        session_db_url=None,
    )
    db = create_session_db(s)
    assert db is not None
    type_name = type(db).__name__
    assert type_name == "SqliteDb"
    assert dbfile.parent.is_dir()
    assert "Sqlite" in session_db_summary(db)


def test_create_session_db_respects_bare_path_url(tmp_path: Path) -> None:
    f = tmp_path / "a.db"
    s = Settings(
        enable_session_db=True,
        session_sqlite_path=Path("ignored.db"),
        session_db_url=str(f),
    )
    db = create_session_db(s)
    assert db is not None
    assert type(db).__name__ == "SqliteDb"


def test_create_session_db_rejects_bad_scheme() -> None:
    s = Settings(
        enable_session_db=True,
        session_db_url="mysql://localhost/db",
    )
    with pytest.raises(ValueError, match="不支持的"):
        create_session_db(s)


def test_get_agent_keeps_db_but_disables_agno_history_when_context_builder_manages_it(
    tmp_path: Path,
) -> None:
    from agent_os.agent.factory import get_agent

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_session_db=True,
        session_sqlite_path=tmp_path / "agent_sess.db",
        session_history_max_messages=10,
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
    )
    assert getattr(ag, "db", None) is not None
    assert getattr(ag, "add_history_to_context", False) is False
    assert getattr(ag, "num_history_messages", None) is None


def test_get_agent_legacy_history_in_context_when_context_builder_disabled(
    tmp_path: Path,
) -> None:
    from agent_os.agent.factory import get_agent

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_context_builder=False,
        enable_session_db=True,
        session_sqlite_path=tmp_path / "agent_sess.db",
        session_history_max_messages=10,
    )
    ag = get_agent(
        ctrl,
        client_id="c1",
        settings=s,
    )
    assert getattr(ag, "db", None) is not None
    assert getattr(ag, "add_history_to_context", False) is True
    assert getattr(ag, "num_history_messages", None) == 10


def test_get_agent_suppresses_double_history_risk_by_default(tmp_path: Path) -> None:
    from agent_os.agent.factory import get_agent

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_context_builder=True,
        context_self_managed_history=False,
        enable_session_db=True,
        session_sqlite_path=tmp_path / "agent_sess.db",
        session_history_max_messages=10,
    )

    ag = get_agent(ctrl, client_id="c1", settings=s)

    assert getattr(ag, "db", None) is not None
    assert getattr(ag, "add_history_to_context", False) is False
    assert getattr(ag, "num_history_messages", None) is None


def test_get_agent_allows_double_history_only_with_explicit_override(tmp_path: Path) -> None:
    from agent_os.agent.factory import get_agent

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_context_builder=True,
        context_self_managed_history=False,
        context_allow_agno_history_with_builder=True,
        enable_session_db=True,
        session_sqlite_path=tmp_path / "agent_sess.db",
        session_history_max_messages=10,
    )

    ag = get_agent(ctrl, client_id="c1", settings=s)

    assert getattr(ag, "db", None) is not None
    assert getattr(ag, "add_history_to_context", False) is True
    assert getattr(ag, "num_history_messages", None) == 10


def test_get_agent_double_history_override_uses_raw_agno_history_cap(tmp_path: Path) -> None:
    from agent_os.agent.factory import get_agent

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_context_builder=True,
        context_self_managed_history=False,
        context_allow_agno_history_with_builder=True,
        enable_session_db=True,
        session_sqlite_path=tmp_path / "agent_sess.db",
        session_history_max_messages=10,
        session_history_cap_when_task_summary=2,
    )

    ag = get_agent(ctrl, client_id="c1", settings=s)

    assert getattr(ag, "add_history_to_context", False) is True
    # This explicit escape hatch delegates raw history to Agno, so it intentionally
    # uses the configured Agno cap rather than ContextBuilder's task-summary cap.
    assert getattr(ag, "num_history_messages", None) == 10


def test_get_agent_no_history_in_context_when_max_zero(tmp_path: Path) -> None:
    from agent_os.agent.factory import get_agent

    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "m.json",
        hindsight_path=tmp_path / "h.jsonl",
    )
    s = Settings(
        enable_session_db=True,
        session_sqlite_path=tmp_path / "z.db",
        session_history_max_messages=0,
    )
    ag = get_agent(ctrl, client_id="c1", settings=s)
    assert ag.db is not None
    assert getattr(ag, "add_history_to_context", True) is False
