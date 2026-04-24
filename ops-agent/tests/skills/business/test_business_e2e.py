"""商业/运营向 skill 专项：用例与 ``tests/skills/short_video`` 完全独立。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ops_agent.evaluator.e2e import run_e2e_eval_file

_FIX = Path(__file__).resolve().parent / "fixtures" / "e2e_pass.json"


@pytest.mark.skill_business
def test_business_golden_e2e_passes() -> None:
    r = run_e2e_eval_file(_FIX)
    assert r.name == "business_ops_skill_smoke"
    assert r.passed, r.violations


@pytest.mark.skill_business
def test_business_fixture_not_sharing_short_video_data() -> None:
    t = _FIX.read_text(encoding="utf-8")
    assert "short_video_skill_smoke" not in t
