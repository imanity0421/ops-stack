from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_os.config import Settings
from agent_os.ingest_gateway import run_ingest_v1
from agent_os.memory.controller import MemoryController


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
        skill_id="default_agent",
        settings=s,
        controller=ctrl,
        mem_kind="fact",
    )
    assert r["status"] == "ok"
    assert "mem0" in r["written_to"] or "local" in str(r).lower()
    data = json.loads((tmp_path / "local.json").read_text(encoding="utf-8"))
    assert "c1" in str(data)
    meta = data["users"]["c1::__client_shared__"]["memories"][0]["metadata"]
    assert meta["scope"] == "client_shared"
    assert meta["memory_source"] == "ingest_gateway"


def test_ingest_mem0_profile_fact_with_user_id_stays_client_shared(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path)
    r = run_ingest_v1(
        target="mem0_profile",
        text="客户固定沟通规则是所有交付先给结论再给依据。",
        client_id="c1",
        user_id="u1",
        skill_id="default_agent",
        settings=Settings(),
        controller=ctrl,
        mem_kind="fact",
    )

    assert r["status"] == "ok"
    data = json.loads((tmp_path / "local.json").read_text(encoding="utf-8"))
    assert "c1::__client_shared__" in data["users"]
    assert "c1::u1" not in data["users"]
    meta = data["users"]["c1::__client_shared__"]["memories"][0]["metadata"]
    assert meta["scope"] == "client_shared"


def test_ingest_hindsight_feedback(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path, hindsight=True)
    s = Settings()
    r = run_ingest_v1(
        target="hindsight",
        text="用户认为首屏节奏慢。",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        settings=s,
        controller=ctrl,
    )
    assert r["status"] == "ok"
    row = json.loads((tmp_path / "h.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["source"] == "ingest_gateway"


def test_ingest_hindsight_supersedes_and_weight(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path, hindsight=True)
    s = Settings()
    r0 = run_ingest_v1(
        target="hindsight",
        text="复盘结论：交付前必须二次核对关键数字与口径。",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        settings=s,
        controller=ctrl,
    )
    assert r0["status"] == "ok"
    prev = json.loads((tmp_path / "h.jsonl").read_text(encoding="utf-8").splitlines()[0])
    eid = prev["event_id"]
    r1 = run_ingest_v1(
        target="hindsight",
        text="复盘结论：交付前必须二次核对关键数字与口径（已补充检查清单）。",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        settings=s,
        controller=ctrl,
        supersedes_event_id=eid,
        weight_count=4,
    )
    assert r1["status"] == "ok"
    lines = (tmp_path / "h.jsonl").read_text(encoding="utf-8").splitlines()
    row1 = json.loads(lines[1])
    assert row1.get("supersedes_event_id") == eid
    assert row1.get("weight_count") == 4


def test_ingest_hindsight_invalid_weight_count_falls_back_to_one(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path, hindsight=True)
    r = run_ingest_v1(
        target="hindsight",
        text="复盘结论：遇到无效权重参数时应继续记录反馈，并按默认权重处理。",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        settings=Settings(),
        controller=ctrl,
        weight_count="bad",  # type: ignore[arg-type]
    )

    assert r["status"] == "ok"
    row = json.loads((tmp_path / "h.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "weight_count" not in row


def test_ingest_hindsight_rejects_when_disabled(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path, hindsight=False)
    s = Settings()
    with pytest.raises(ValueError, match="Hindsight"):
        run_ingest_v1(
            target="hindsight",
            text="x",
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
            settings=s,
            controller=ctrl,
        )


def test_ingest_mem0_policy_rejection_reports_rejected(tmp_path: Path) -> None:
    ctrl = _ctrl(tmp_path)
    r = run_ingest_v1(
        target="mem0_profile",
        text="哈哈我开玩笑的，暂时随便说说",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        settings=Settings(),
        controller=ctrl,
        mem_kind="fact",
    )
    assert r["status"] == "rejected"
    assert r["policy_rejected"] is True
    assert r["written_to"] == []


def test_ingest_asset_store_minimal_with_allow_llm_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # asset_store 依赖 LanceDB；全量单测与 CI 使用 pip install -e ".[dev]"（无则此处 ImportError，不 skip）
    import lancedb  # noqa: F401

    monkeypatch.setenv("AGENT_OS_INGEST_ALLOW_LLM", "0")
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
        f"行{i:03d} 通用交付案例需在第{i % 7 + 1}步明确目标；内容包含背景、约束与行动项，用于测入库。"
        for i in range(40)
    )
    r = run_ingest_v1(
        target="asset_store",
        text=raw,
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
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
            skill_id="default_agent",
            settings=Settings(),
            controller=_ctrl(tmp_path, hindsight=True),
        )


def test_ingest_mem0_profile_rejects_invalid_mem_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mem_kind"):
        run_ingest_v1(
            target="mem0_profile",
            text="客户偏好先给结论再给依据。",
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
            settings=Settings(),
            controller=_ctrl(tmp_path, hindsight=True),
            mem_kind="unknown_kind",
        )
