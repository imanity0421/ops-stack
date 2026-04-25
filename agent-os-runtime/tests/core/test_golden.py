from __future__ import annotations

import json
from pathlib import Path

from agent_os.evaluator.golden import check_violations, load_golden_rules


def test_load_rules_filters_bad(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text(json.dumps([{"pattern": "a", "message": "m"}, {"foo": 1}]), encoding="utf-8")
    rules = load_golden_rules(p)
    assert len(rules) == 1


def test_check_violations() -> None:
    rules = [{"id": "x", "pattern": "\\d+元", "message": "no price"}]
    assert check_violations("无数字", rules) == []
    assert len(check_violations("售价99元", rules)) == 1


def test_invalid_regex_skipped() -> None:
    rules = [{"pattern": "(", "message": "bad"}]
    assert check_violations("(", rules) == []
