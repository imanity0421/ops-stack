from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ops_agent.config import Settings
from ops_agent.ingest_gateway import run_ingest_v1
from ops_agent.memory.controller import MemoryController


def _ctrl(tmp_path: Path, *, hindsight: bool = True) -> MemoryController:
    return MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "local.json",
        hindsight_path=tmp_path / "h.jsonl",
        enable_hindsight=hindsight,
    )


def test_ingest_mem0_profile_fact(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path)
    s = Settings()
    r = run_ingest_v1(
        target="mem0_profile",
        text="客户偏好晚上八点后沟通。",
        client_id="c1",
        user_id=None,
        skill_id="default_ops",
        settings=s,
        controller=ctrl,
        mem_kind="fact",
    )
    assert r["status"] == "ok"
    assert "mem0" in r["written_to"] or "local" in str(r).lower()
    data = json.loads((tmp_path / "local.json").read_text(encoding="utf-8"))
    assert "c1" in str(data)


def test_ingest_hindsight_feedback(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path, hindsight=True)
    s = Settings()
    r = run_ingest_v1(
        target="hindsight",
        text="用户认为首屏节奏慢。",
        client_id="c1",
        user_id=None,
        skill_id="default_ops",
        settings=s,
        controller=ctrl,
    )
    assert r["status"] == "ok"
    assert (tmp_path / "h.jsonl").read_text(encoding="utf-8").strip()


def test_ingest_hindsight_rejects_when_disabled(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path, hindsight=False)
    s = Settings()
    with pytest.raises(ValueError, match="Hindsight"):
        run_ingest_v1(
            target="hindsight",
            text="x",
            client_id="c1",
            user_id=None,
            skill_id="default_ops",
            settings=s,
            controller=ctrl,
        )


def test_ingest_asset_store_minimal_with_allow_llm_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # asset_store 依赖 LanceDB；全量单测与 CI 使用 pip install -e ".[dev]"（无则此处 ImportError，不 skip）
    import lancedb  # noqa: F401

    monkeypatch.setenv("OPS_INGEST_ALLOW_LLM", "0")
    adir = tmp_path / "lance"
    s = replace(
        Settings.from_env(),
        enable_asset_store=True,
        asset_store_path=adir,
        skill_compliance_dir=None,
    )
    ctrl = _ctrl(tmp_path, hindsight=True)
    # ``_basic_validate`` 会拒绝低熵短字符集，故用多行互不重复长串
    raw = "\n".join(
        f"行{i:03d} 短视频口播需前{i % 7 + 1}秒抓人；案例含产品卖点、场景与 CTA 变体，用于测入库。"
        for i in range(40)
    )
    r = run_ingest_v1(
        target="asset_store",
        text=raw,
        client_id="c1",
        user_id=None,
        skill_id="default_ops",
        settings=s,
        controller=ctrl,
    )
    assert r["target"] == "asset_store"
    st = r.get("result", {}).get("status")
    assert st in ("ok", "quarantined", "duplicate_skip")


def test_ingest_unknown_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知 target"):
        run_ingest_v1(
            target="not_a_valid_target_string",
            text="a",
            client_id="c",
            user_id=None,
            skill_id="default_ops",
            settings=Settings(),
            controller=_ctrl(tmp_path, hindsight=True),
        )
