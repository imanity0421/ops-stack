from ops_agent.doctor import run_doctor


def test_doctor_strict_no_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert run_doctor(strict=True) == 1


def test_doctor_non_strict(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert run_doctor(strict=False) == 0


def test_doctor_ok_with_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert run_doctor(strict=True) == 0
