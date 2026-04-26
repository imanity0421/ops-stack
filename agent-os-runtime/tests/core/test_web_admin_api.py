from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_os.config import Settings
from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemoryLane, UserFact
from examples import web_chat_fastapi as web


def _setup_admin_env(monkeypatch, ent_path: Path, audit_path: Path) -> None:
    monkeypatch.setenv("AGENT_OS_WEB_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("AGENT_OS_WEB_ADMIN_API_TOKEN", "tok_test")
    monkeypatch.setenv("AGENT_OS_WEB_ADMIN_ALLOWED_HOSTS", "testclient,127.0.0.1,::1,localhost")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", str(ent_path))
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("AGENT_OS_WEB_ADMIN_IDEMPOTENCY_ENABLED", "1")


def _stub_memory_bundle(monkeypatch, local_path: Path) -> None:
    settings = Settings(mem0_api_key=None, local_memory_path=local_path)
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )


def test_web_admin_auth_chain(tmp_path: Path, monkeypatch) -> None:
    ent = tmp_path / "ent.json"
    audit = tmp_path / "audit.jsonl"
    _setup_admin_env(monkeypatch, ent, audit)
    c = TestClient(web.app)

    r0 = c.get("/api/admin/graphiti-entitlements")
    assert r0.status_code == 401

    r1 = c.get("/api/admin/graphiti-entitlements", headers={"x-admin-token": "bad"})
    assert r1.status_code == 403

    r2 = c.get("/api/admin/graphiti-entitlements", headers={"x-admin-token": "tok_test"})
    assert r2.status_code == 200
    data = r2.json()["data"]
    assert data["revision"] == 0
    assert data["client_entitlements"] == {}


def test_web_admin_conflict_and_audit_chain(tmp_path: Path, monkeypatch) -> None:
    ent = tmp_path / "ent.json"
    audit = tmp_path / "audit.jsonl"
    _setup_admin_env(monkeypatch, ent, audit)
    c = TestClient(web.app)
    headers = {"x-admin-token": "tok_test", "x-admin-actor": "web_tester"}

    r0 = c.get("/api/admin/graphiti-entitlements", headers=headers)
    assert r0.status_code == 200
    rev0 = r0.json()["data"]["revision"]

    r1 = c.post(
        "/api/admin/graphiti-entitlements/global",
        headers=headers,
        json={"skills": ["default_agent"], "expected_revision": rev0},
    )
    assert r1.status_code == 200
    rev1 = r1.json()["data"]["revision"]
    assert rev1 == rev0 + 1

    r_conflict = c.post(
        "/api/admin/graphiti-entitlements/client",
        headers=headers,
        json={"client_id": "c1", "skills": ["default_agent"], "expected_revision": rev0},
    )
    assert r_conflict.status_code == 409
    detail = r_conflict.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["actual_revision"] == rev1

    r2 = c.post(
        "/api/admin/graphiti-entitlements/client",
        headers=headers,
        json={"client_id": "c1", "skills": ["default_agent"], "expected_revision": rev1},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["revision"] == rev1 + 1

    rows = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 2
    assert rows[0]["action"] == "set_global"
    assert rows[1]["action"] == "set_client"
    assert rows[0]["actor"] == "web_tester"


def test_web_admin_idempotency_key_dedupes_side_effects(tmp_path: Path, monkeypatch) -> None:
    ent = tmp_path / "ent.json"
    audit = tmp_path / "audit.jsonl"
    _setup_admin_env(monkeypatch, ent, audit)
    c = TestClient(web.app)
    headers = {
        "x-admin-token": "tok_test",
        "x-admin-actor": "web_tester",
        "Idempotency-Key": "dup-001",
    }

    r0 = c.get("/api/admin/graphiti-entitlements", headers={"x-admin-token": "tok_test"})
    rev0 = r0.json()["data"]["revision"]
    payload = {"skills": ["default_agent"], "expected_revision": rev0}

    r1 = c.post("/api/admin/graphiti-entitlements/global", headers=headers, json=payload)
    assert r1.status_code == 200
    rev1 = r1.json()["data"]["revision"]

    r2 = c.post("/api/admin/graphiti-entitlements/global", headers=headers, json=payload)
    assert r2.status_code == 200
    assert r2.json()["data"]["revision"] == rev1

    rows = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1


def test_web_admin_idempotency_key_reuse_with_different_payload_conflicts(
    tmp_path: Path, monkeypatch
) -> None:
    ent = tmp_path / "ent.json"
    audit = tmp_path / "audit.jsonl"
    _setup_admin_env(monkeypatch, ent, audit)
    c = TestClient(web.app)
    headers = {"x-admin-token": "tok_test", "Idempotency-Key": "dup-002"}
    r0 = c.get("/api/admin/graphiti-entitlements", headers={"x-admin-token": "tok_test"})
    rev0 = r0.json()["data"]["revision"]

    r1 = c.post(
        "/api/admin/graphiti-entitlements/global",
        headers=headers,
        json={"skills": ["default_agent"], "expected_revision": rev0},
    )
    assert r1.status_code == 200
    r2 = c.post(
        "/api/admin/graphiti-entitlements/global",
        headers=headers,
        json={"skills": ["short_video"], "expected_revision": rev0},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "idempotency_key_reused"


def test_web_memory_profile_list_tolerates_bad_local_json(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local_memory.json"
    local.write_text("{bad", encoding="utf-8")
    _stub_memory_bundle(monkeypatch, local)
    c = TestClient(web.app)

    r = c.get("/api/memory/profile/list", params={"client_id": "c1"})

    assert r.status_code == 200
    assert r.json()["items"] == []


def test_web_memory_profile_list_tolerates_bad_utf8(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local_memory.json"
    local.write_bytes(b"\xff\xfe\x00")
    _stub_memory_bundle(monkeypatch, local)
    c = TestClient(web.app)

    r = c.get("/api/memory/profile/list", params={"client_id": "c1"})

    assert r.status_code == 200
    assert r.json()["items"] == []


def test_web_memory_profile_list_accepts_utf8_bom(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local_memory.json"
    local.write_text(
        "\ufeff"
        + json.dumps(
            {
                "users": {
                    "c1": {
                        "memories": [
                            {"text": "hello", "metadata": {"source": "unit"}},
                            "bad-row",
                        ]
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _stub_memory_bundle(monkeypatch, local)
    c = TestClient(web.app)

    r = c.get("/api/memory/profile/list", params={"client_id": "c1"})

    assert r.status_code == 200
    assert r.json()["items"] == [{"index": 0, "text": "hello", "metadata": {"source": "unit"}}]


def test_web_memory_profile_delete_tolerates_non_object_local_json(
    tmp_path: Path, monkeypatch
) -> None:
    local = tmp_path / "local_memory.json"
    local.write_text("[]", encoding="utf-8")
    _stub_memory_bundle(monkeypatch, local)
    c = TestClient(web.app)

    r = c.post(
        "/api/memory/profile/delete-local",
        json={"client_id": "c1", "index": 0},
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "index 越界"


def test_web_memory_hindsight_list_accepts_utf8_bom(tmp_path: Path, monkeypatch) -> None:
    hind = tmp_path / "hindsight.jsonl"
    hind.write_text(
        "\ufeff"
        + json.dumps({"client_id": "c1", "text": "hello hindsight"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    settings = Settings(mem0_api_key=None, hindsight_path=hind)
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )
    c = TestClient(web.app)

    r = c.get("/api/memory/hindsight/list", params={"client_id": "c1"})

    assert r.status_code == 200
    assert r.json()["items"][0]["row"]["text"] == "hello hindsight"


def test_web_memory_hindsight_list_tolerates_bad_utf8(tmp_path: Path, monkeypatch) -> None:
    hind = tmp_path / "hindsight.jsonl"
    hind.write_bytes(b"\xff\xfe\x00")
    settings = Settings(mem0_api_key=None, hindsight_path=hind)
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )
    c = TestClient(web.app)

    r = c.get("/api/memory/hindsight/list", params={"client_id": "c1"})

    assert r.status_code == 200
    assert r.json()["items"] == []


def test_web_memory_hindsight_list_skips_non_object_rows(tmp_path: Path, monkeypatch) -> None:
    hind = tmp_path / "hindsight.jsonl"
    hind.write_text(
        '["not", "object"]\n'
        + json.dumps({"client_id": "c1", "text": "valid hindsight"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    settings = Settings(mem0_api_key=None, hindsight_path=hind)
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )
    c = TestClient(web.app)

    r = c.get("/api/memory/hindsight/list", params={"client_id": "c1"})

    assert r.status_code == 200
    assert [x["row"]["text"] for x in r.json()["items"]] == ["valid hindsight"]


def test_web_memory_hindsight_search_exposes_debug_scores_when_requested(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_admin_env(monkeypatch, tmp_path / "ent.json", tmp_path / "audit.jsonl")
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    settings = Settings(mem0_api_key=None, local_memory_path=local, hindsight_path=hindsight)
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            user_id="u1",
            skill_id="default_agent",
            text="复盘结论：交付前必须确认关键约束。",
            fact_type="feedback",
        )
    )
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, object()))
    c = TestClient(web.app)

    normal = c.get(
        "/api/memory/hindsight/search",
        params={"client_id": "c1", "user_id": "u1", "query": "关键约束"},
    )
    debug = c.get(
        "/api/memory/hindsight/search",
        headers={"x-admin-token": "tok_test"},
        params={
            "client_id": "c1",
            "user_id": "u1",
            "query": "关键约束",
            "debug_scores": "true",
        },
    )

    assert normal.status_code == 200
    assert normal.json()["debug_scores"] is False
    assert "score=" not in normal.json()["items"][0]
    assert debug.status_code == 200
    assert debug.json()["debug_scores"] is True
    assert "score=" in debug.json()["items"][0]
    assert "reasons=" in debug.json()["items"][0]


def test_web_memory_hindsight_debug_search_requires_admin(
    tmp_path: Path, monkeypatch
) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    settings = Settings(mem0_api_key=None, local_memory_path=local, hindsight_path=hindsight)
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, object()))
    c = TestClient(web.app)

    r = c.get(
        "/api/memory/hindsight/search",
        params={"client_id": "c1", "query": "关键约束", "debug_scores": "true"},
    )

    assert r.status_code == 403


def test_web_memory_hindsight_delete_rejects_bad_utf8(tmp_path: Path, monkeypatch) -> None:
    hind = tmp_path / "hindsight.jsonl"
    hind.write_bytes(b"\xff\xfe\x00")
    settings = Settings(mem0_api_key=None, hindsight_path=hind)
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )
    c = TestClient(web.app)

    r = c.post(
        "/api/memory/hindsight/delete-line",
        json={"client_id": "c1", "file_line": 1},
    )

    assert r.status_code == 400
    assert "无法读取" in r.json()["detail"]


def test_web_memory_hindsight_delete_invalidates_sidecar_index(
    tmp_path: Path, monkeypatch
) -> None:
    local = tmp_path / "local.json"
    hind = tmp_path / "hindsight.jsonl"
    settings = Settings(mem0_api_key=None, local_memory_path=local, hindsight_path=hind)
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hind,
    )
    ctrl.ingest_user_fact(
        UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id="c1",
            text="待删除教训：交付前必须确认关键约束。",
            fact_type="feedback",
        )
    )
    assert ctrl.search_hindsight("关键约束", client_id="c1")
    index_path = hind.with_name(f"{hind.name}.index.json")
    assert index_path.is_file()
    assert "待删除教训" in index_path.read_text(encoding="utf-8")
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, object()))
    c = TestClient(web.app)

    r = c.post(
        "/api/memory/hindsight/delete-line",
        json={"client_id": "c1", "file_line": 1},
    )

    assert r.status_code == 200
    assert not index_path.exists()


def test_web_memory_hindsight_delete_uses_store_delete_line(tmp_path: Path, monkeypatch) -> None:
    hind = tmp_path / "hindsight.jsonl"
    hind.write_text(
        '{"event_id":"e1","type":"feedback","client_id":"c1","text":"待删教训"}\n',
        encoding="utf-8",
    )
    settings = Settings(mem0_api_key=None, hindsight_path=hind)
    called = {}

    class _Store:
        def __init__(self, path, **kwargs):
            called["path"] = path
            called["kwargs"] = kwargs

        def delete_line(self, **kwargs):
            called["delete_kwargs"] = kwargs
            return {"status": "ok", "json_index_removed": True, "vector_index_removed": True}

    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )
    monkeypatch.setattr(web, "HindsightStore", _Store)
    c = TestClient(web.app)

    r = c.post(
        "/api/memory/hindsight/delete-line",
        json={"client_id": "c1", "file_line": 1},
    )

    assert r.status_code == 200
    assert called["kwargs"]["enable_vector_recall"] is True
    assert called["delete_kwargs"] == {"file_line": 1, "expected_client_id": "c1"}


def test_web_memory_hindsight_delete_rejects_non_object_row(tmp_path: Path, monkeypatch) -> None:
    hind = tmp_path / "hindsight.jsonl"
    hind.write_text('["not", "object"]\n', encoding="utf-8")
    settings = Settings(mem0_api_key=None, hindsight_path=hind)
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (settings, object(), object())
    )
    c = TestClient(web.app)

    r = c.post(
        "/api/memory/hindsight/delete-line",
        json={"client_id": "c1", "file_line": 1},
    )

    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "not_json_object"


def test_web_session_end_passes_controller_to_async_review(monkeypatch) -> None:
    class _Ctrl:
        hindsight_store = object()

    class _Review:
        controller_seen = None
        submitted = False

        @classmethod
        def from_env(cls, controller):
            cls.controller_seen = controller
            return cls()

        def submit_and_wait(self, **kwargs):
            _ = kwargs
            type(self).submitted = True
            return {"status": "ok"}

    ctrl = _Ctrl()
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (Settings(), ctrl, object()))
    monkeypatch.setattr(web, "AsyncReviewService", _Review)
    monkeypatch.setattr(web, "_transcripts", {"s1": [("user", "方向不对"), ("assistant", "我调整")]})
    c = TestClient(web.app)

    r = c.post(
        "/api/session/end",
        json={
            "session_id": "s1",
            "client_id": "c1",
            "user_id": "u1",
            "run_review": True,
        },
    )

    assert r.status_code == 200
    assert r.json()["review"] == "ok"
    assert _Review.controller_seen is ctrl
    assert _Review.submitted is True


def test_web_port_invalid_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OS_WEB_PORT", "not-a-port")

    assert web._web_port_from_env() == 8765
