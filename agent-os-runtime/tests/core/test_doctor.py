from agent_os.config import Settings
from agent_os.doctor import run_doctor
from agent_os.knowledge.graphiti_reader import GraphitiReadService


def test_doctor_strict_no_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert run_doctor(strict=True) == 1


def test_doctor_non_strict(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert run_doctor(strict=False) == 0


def test_doctor_ok_with_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert run_doctor(strict=True) == 0


def test_settings_invalid_numeric_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OS_SESSION_HISTORY_MAX_MESSAGES", "not-an-int")
    monkeypatch.setenv("AGENT_OS_SNAPSHOT_EVERY_N_TURNS", "not-an-int")
    monkeypatch.setenv("AGENT_OS_TASK_SUMMARY_MAX_CHARS", "not-an-int")

    s = Settings.from_env()

    assert s.session_history_max_messages == 20
    assert s.snapshot_every_n_turns == 5
    assert s.task_summary_max_chars == 800


def test_graphiti_invalid_numeric_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OS_GRAPHITI_SEARCH_TIMEOUT_SEC", "bad")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_MAX_RESULTS", "bad")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_BFS_MAX_DEPTH", "bad")

    svc = GraphitiReadService.from_env(None)

    assert svc._timeout_sec == 20.0
    assert svc._max_results == 12
    assert svc._bfs_max_depth == 2
