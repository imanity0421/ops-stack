from __future__ import annotations

from pathlib import Path

from ops_agent.evaluator.e2e import run_e2e_eval_file


def test_e2e_eval_fixture_fails_rules() -> None:
    p = Path(__file__).resolve().parent / "fixtures" / "e2e_eval_case.json"
    r = run_e2e_eval_file(p)
    assert r.passed is False
    assert r.violations
