import pytest

from ops_agent.util.retry import retry_sync


def test_retry_sync_succeeds_first() -> None:
    n = {"c": 0}

    def ok() -> int:
        n["c"] += 1
        return 42

    assert retry_sync(ok, attempts=3, label="t") == 42
    assert n["c"] == 1


def test_retry_sync_eventually_succeeds() -> None:
    n = {"c": 0}

    def flaky() -> str:
        n["c"] += 1
        if n["c"] < 2:
            raise ConnectionError("transient")
        return "ok"

    assert retry_sync(flaky, attempts=3, base_delay_sec=0.01, label="t") == "ok"


def test_retry_sync_exhausts() -> None:
    def always_fail() -> None:
        raise ConnectionError("x")

    with pytest.raises(ConnectionError):
        retry_sync(always_fail, attempts=2, base_delay_sec=0.01, label="t")
