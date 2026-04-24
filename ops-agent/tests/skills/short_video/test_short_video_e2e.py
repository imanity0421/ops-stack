"""短视频 skill 专项：与 ``tests/skills/business`` 数据隔离，仅复用 ``run_e2e_eval_*`` 引擎。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ops_agent.evaluator.e2e import run_e2e_eval_file

_FIX = Path(__file__).resolve().parent / "fixtures" / "e2e_pass.json"


@pytest.mark.skill_short_video
def test_short_video_golden_e2e_passes() -> None:
    r = run_e2e_eval_file(_FIX)
    assert r.name == "short_video_skill_smoke"
    assert r.passed, r.violations


@pytest.mark.skill_short_video
def test_short_video_uses_isolated_fixture_path() -> None:
    assert _FIX.is_file()
    assert "short_video" in _FIX.read_text(encoding="utf-8")
