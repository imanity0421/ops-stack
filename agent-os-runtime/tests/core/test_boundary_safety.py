"""边界与负向输入：超长字符串、非法 query 等（非平台化，仅防崩溃/失控内存）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.config import Settings
from agent_os.ingest_gateway import INGEST_V1_MAX_TEXT_CHARS, run_ingest_v1
from agent_os.memory.controller import MemoryController
from examples import web_chat_fastapi as web


def test_ingest_v1_rejects_text_over_max_chars(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "local.json",
        hindsight_path=tmp_path / "h.jsonl",
        enable_hindsight=True,
    )
    huge = "a" * (INGEST_V1_MAX_TEXT_CHARS + 1)
    with pytest.raises(ValueError, match="过长"):
        run_ingest_v1(
            target="mem0_profile",
            text=huge,
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
            settings=Settings(),
            controller=ctrl,
            mem_kind="fact",
        )


def test_ingest_v1_accepts_text_at_exact_max_for_hindsight(tmp_path: Path) -> None:
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "local.json",
        hindsight_path=tmp_path / "h.jsonl",
        enable_hindsight=True,
    )
    base = "用户认为首屏反馈区域需要可观测指标以便后续复盘改进。"
    pad = INGEST_V1_MAX_TEXT_CHARS - len(base)
    assert pad > 0
    text = base + ("x" * pad)
    assert len(text) == INGEST_V1_MAX_TEXT_CHARS
    r = run_ingest_v1(
        target="hindsight",
        text=text,
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        settings=Settings(),
        controller=ctrl,
    )
    assert r["status"] == "ok"


def test_web_post_ingest_rejects_oversized_json_body() -> None:
    c = TestClient(web.app)
    payload = {
        "target": "mem0_profile",
        "client_id": "c1",
        "text": "x" * (INGEST_V1_MAX_TEXT_CHARS + 1),
    }
    r = c.post("/ingest", json=payload)
    assert r.status_code == 422


def test_web_post_chat_rejects_oversized_message() -> None:
    c = TestClient(web.app)
    r = c.post(
        "/chat",
        json={"message": "m" * (INGEST_V1_MAX_TEXT_CHARS + 1), "client_id": "c1"},
    )
    assert r.status_code == 422


def test_web_post_memory_ingest_rejects_oversized_text() -> None:
    c = TestClient(web.app)
    r = c.post(
        "/api/memory/ingest",
        json={
            "client_id": "c1",
            "text": "t" * (INGEST_V1_MAX_TEXT_CHARS + 1),
            "kind": "fact",
        },
    )
    assert r.status_code == 422


def test_web_hindsight_search_rejects_query_over_max(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(mem0_api_key=None, local_memory_path=tmp_path / "l.json")
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=tmp_path / "l.json",
        hindsight_path=tmp_path / "h.jsonl",
        enable_hindsight=True,
    )
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, object()))
    c = TestClient(web.app)
    r = c.get(
        "/api/memory/hindsight/search",
        params={"client_id": "c1", "query": "q" * 8193},
    )
    assert r.status_code == 422


def test_web_profile_list_rejects_empty_client_id() -> None:
    c = TestClient(web.app)
    r = c.get("/api/memory/profile/list", params={"client_id": ""})
    assert r.status_code == 422
