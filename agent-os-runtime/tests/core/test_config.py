from __future__ import annotations

from agent_os.config import Settings


def test_settings_reads_hindsight_vector_candidate_limit(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OS_HINDSIGHT_VECTOR_CANDIDATE_LIMIT", "42")

    settings = Settings.from_env()

    assert settings.hindsight_vector_candidate_limit == 42
