from __future__ import annotations

import json
from pathlib import Path

from agent_os.evaluator.e2e import run_e2e_eval_file


def test_e2e_eval_fixture_fails_rules() -> None:
    p = Path(__file__).resolve().parent / "fixtures" / "e2e_eval_case.json"
    r = run_e2e_eval_file(p)
    assert r.passed is False
    assert r.violations


def test_e2e_eval_bad_json_returns_failed_report(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{bad", encoding="utf-8")

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert "无法读取或解析" in r.violations[0]


def test_e2e_eval_non_object_returns_failed_report(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("[]", encoding="utf-8")

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert r.violations == ["评测文件顶层须为 JSON 对象"]


def test_e2e_eval_non_string_turns_return_failed_report(tmp_path: Path) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        json.dumps(
            {
                "assistant_turns": [None, 123, {"text": "safe"}],
                "golden_rules": [{"pattern": "safe", "message": "hit"}],
            }
        ),
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert r.violations == ["缺少可检查的 assistant_turns"]
    assert r.assistant_turns_checked == 0


def test_e2e_eval_missing_golden_rules_path_returns_failed_report(
    tmp_path: Path,
) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        '{"assistant_turns":["safe"],"golden_rules_path":"missing_rules.json"}',
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert "golden_rules_path 不存在" in r.violations[0]


def test_e2e_eval_empty_golden_rules_path_returns_failed_report(
    tmp_path: Path,
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text("[]", encoding="utf-8")
    p = tmp_path / "case.json"
    p.write_text(
        json.dumps({"assistant_turns": ["safe"], "golden_rules_path": str(rules)}),
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert "golden_rules_path 无可用规则" in r.violations[0]


def test_e2e_eval_empty_inline_golden_rules_returns_failed_report(
    tmp_path: Path,
) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        json.dumps({"assistant_turns": ["safe"], "golden_rules": []}),
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert r.violations == ["golden_rules 无可用规则"]


def test_e2e_eval_invalid_inline_golden_rules_returns_failed_report(
    tmp_path: Path,
) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        json.dumps({"assistant_turns": ["safe"], "golden_rules": [{"foo": "bar"}]}),
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert r.violations == ["golden_rules 无可用规则"]


def test_e2e_eval_empty_or_invalid_patterns_are_not_usable_rules(
    tmp_path: Path,
) -> None:
    p = tmp_path / "case.json"
    p.write_text(
        json.dumps(
            {
                "assistant_turns": ["safe"],
                "golden_rules": [
                    {"pattern": "", "message": "empty"},
                    {"pattern": "[", "message": "bad regex"},
                ],
            }
        ),
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert r.violations == ["golden_rules 无可用规则"]


def test_e2e_eval_relative_golden_rules_path_resolves_from_case_file(
    tmp_path: Path,
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps([{"pattern": "forbidden", "message": "hit relative rule"}]),
        encoding="utf-8",
    )
    p = tmp_path / "case.json"
    p.write_text(
        json.dumps(
            {
                "assistant_turns": ["this contains forbidden text"],
                "golden_rules_path": "rules.json",
            }
        ),
        encoding="utf-8",
    )

    r = run_e2e_eval_file(p)

    assert r.passed is False
    assert r.violations == ["hit relative rule"]
