from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_os.agent.task_memory import TaskMemoryStore, TaskSummary
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


class _ChatCtrl:
    def __init__(self) -> None:
        self.bumped: list[tuple[str, str | None]] = []

    def bump_turn_and_maybe_snapshot(self, client_id: str, user_id: str | None) -> None:
        self.bumped.append((client_id, user_id))


class _ChatAgent:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.db = None

    def run(self, message: str, **kwargs):
        self.messages.append(message)
        return SimpleNamespace(content=f"echo:{message}", metrics=None, tools=None, extra_data=None)


def _stub_chat_bundle(monkeypatch) -> tuple[_ChatCtrl, _ChatAgent]:
    ctrl = _ChatCtrl()
    agent = _ChatAgent()
    settings = Settings(mem0_api_key=None, enable_context_builder=False)
    monkeypatch.setattr(web, "_transcripts", {})
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    return ctrl, agent


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


def test_web_chat_rejects_blank_message_without_running_agent(monkeypatch) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    c = TestClient(web.app)

    r = c.post("/chat", json={"message": " \n\t ", "client_id": "c1"})

    assert r.status_code == 400
    assert r.json()["detail"] == "message 不能为空"
    assert ctrl.bumped == []
    assert agent.messages == []


def test_web_chat_rejects_empty_client_id(monkeypatch) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    c = TestClient(web.app)

    r = c.post("/chat", json={"message": "hello", "client_id": ""})

    assert r.status_code == 422
    assert ctrl.bumped == []
    assert agent.messages == []


def test_web_chat_rejects_oversized_message(monkeypatch) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    c = TestClient(web.app)

    r = c.post("/chat", json={"message": "x" * (web.INGEST_V1_MAX_TEXT_CHARS + 1)})

    assert r.status_code == 422
    assert ctrl.bumped == []
    assert agent.messages == []


def test_web_chat_handles_unusual_characters_without_context_builder(monkeypatch) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    c = TestClient(web.app)
    msg = "零宽\u200b字符\x00控制符 <xml> & emoji-like text"

    r = c.post(
        "/chat",
        headers={"X-Correlation-ID": "corr-001"},
        json={
            "message": msg,
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "corr-001"
    body = r.json()
    assert body["session_id"] == "s1"
    assert body["trace"] is None
    assert body["reply"] == f"echo:{msg}"
    assert agent.messages == [msg]
    assert ctrl.bumped == [("c1", None)]
    assert [x["role"] for x in body["history"]] == ["user", "assistant"]


def test_web_chat_context_builder_prefers_persisted_session_history(monkeypatch) -> None:
    ctrl = _ChatCtrl()

    class PersistedAgent(_ChatAgent):
        def __init__(self) -> None:
            super().__init__()
            self.db = object()

        def get_session_messages(self, **kwargs):
            assert kwargs["session_id"] == "s1"
            return [SimpleNamespace(role="user", content="已持久化的上一轮上下文")]

    agent = PersistedAgent()
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=False,
        enable_ephemeral_metadata=False,
        session_history_max_messages=5,
        context_estimate_tokens=False,
    )
    monkeypatch.setattr(web, "_transcripts", {})
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "继续",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert "<recent_history>" in agent.messages[0]
    assert "已持久化的上一轮上下文" in agent.messages[0]
    assert "继续" in agent.messages[0]


def test_web_chat_context_builder_falls_back_to_in_memory_transcript(monkeypatch) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=False,
        enable_ephemeral_metadata=False,
        session_history_max_messages=5,
        context_estimate_tokens=False,
    )
    monkeypatch.setattr(web, "_transcripts", {"s1": [("user", "内存上一轮")]})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "继续",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert "<recent_history>" in agent.messages[0]
    assert "内存上一轮" in agent.messages[0]


def test_web_chat_trace_includes_context_diagnostics(monkeypatch) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=False,
        enable_ephemeral_metadata=False,
        context_estimate_tokens=False,
    )
    monkeypatch.setattr(web, "_transcripts", {"s1": [("user", "内存上一轮")]})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "继续",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": True,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    diag = r.json()["trace"]["context_diagnostics"]
    assert diag["total_chars"] > 0
    assert any(b["name"] == "recent_history" for b in diag["blocks"])
    assert any(b["name"] == "current_user_message" for b in diag["blocks"])


def test_web_chat_auto_retrieve_trace_absent_when_global_auto_retrieve_disabled(
    monkeypatch,
) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=False,
        context_auto_retrieve_mode="keywords",
        enable_ephemeral_metadata=False,
        context_estimate_tokens=False,
        context_trace_log=True,
    )
    traces = []

    def capture_trace(**kwargs):
        traces.append(kwargs["trace"])

    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    monkeypatch.setattr(web, "log_context_management_trace", capture_trace)
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "请给我一个方案",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert traces
    assert "auto_retrieve" not in traces[0].to_obs_log_line()


def test_web_chat_context_builder_injects_task_memory_and_caps_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ctrl = _ChatCtrl()

    class PersistedAgent(_ChatAgent):
        def __init__(self) -> None:
            super().__init__()
            self.db = object()
            self.history_limit: int | None = None

        def get_session_messages(self, **kwargs):
            self.history_limit = kwargs["limit"]
            return [SimpleNamespace(role="user", content="持久化历史")]

    agent = PersistedAgent()
    task_db = tmp_path / "task.db"
    store = TaskMemoryStore(task_db)
    task = store.get_or_create_active_task(
        session_id="s1",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        seed_message="既有任务",
    )
    store.upsert_summary(
        TaskSummary(
            session_id="s1",
            task_id=task.task_id,
            summary_text="- 当前任务目标：Web 侧复用 TaskMemory",
            summary_version=1,
            covered_message_count=8,
            updated_at="2026-04-27T00:00:00+00:00",
        )
    )
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=False,
        enable_ephemeral_metadata=False,
        enable_task_memory=True,
        task_memory_sqlite_path=task_db,
        session_history_max_messages=8,
        session_history_cap_when_task_summary=2,
        context_estimate_tokens=False,
    )
    monkeypatch.setattr(web, "_transcripts", {})
    monkeypatch.setattr(web, "_bundles", {})
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "继续",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert "<working_memory>" in agent.messages[0]
    assert "Web 侧复用 TaskMemory" in agent.messages[0]
    assert "既有任务" in agent.messages[0]
    assert agent.history_limit == 2
    messages = store.task_messages(session_id="s1", task_id=task.task_id)
    assert [m.role for m in messages[-2:]] == ["user", "assistant"]


def test_web_chat_auto_retrieve_uses_asset_store_settings(monkeypatch, tmp_path: Path) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=True,
        context_auto_retrieve_mode="always",
        enable_ephemeral_metadata=False,
        context_estimate_tokens=False,
        enable_asset_store=True,
        asset_store_path=tmp_path / "asset_store.lancedb",
    )
    fake_asset_store = object()
    captured: dict[str, object] = {}

    def fake_asset_store_from_settings(*, enable: bool, path: Path):
        captured["asset_factory_enable"] = enable
        captured["asset_factory_path"] = path
        return fake_asset_store

    def fake_auto_retrieve(*args, **kwargs):
        captured["enable_asset_store"] = kwargs["enable_asset_store"]
        captured["asset_store"] = kwargs["asset_store"]
        return (
            "<ordered_context><asset_references>Web Asset 命中</asset_references></ordered_context>"
        )

    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    monkeypatch.setattr(web, "asset_store_from_settings", fake_asset_store_from_settings)
    monkeypatch.setattr(web, "build_auto_retrieval_context", fake_auto_retrieve)
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "你好",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert captured["asset_factory_enable"] is True
    assert captured["asset_factory_path"] == settings.asset_store_path
    assert captured["enable_asset_store"] is True
    assert captured["asset_store"] is fake_asset_store
    assert "Web Asset 命中" in agent.messages[0]


def test_web_chat_auto_retrieve_reuses_bundle_context_sources(monkeypatch, tmp_path: Path) -> None:
    ctrl, agent = _stub_chat_bundle(monkeypatch)
    settings = Settings(
        mem0_api_key=None,
        enable_context_builder=True,
        enable_context_auto_retrieve=True,
        context_auto_retrieve_mode="always",
        enable_ephemeral_metadata=False,
        context_estimate_tokens=False,
        enable_asset_store=True,
        asset_store_path=tmp_path / "asset_store.lancedb",
    )
    bundle_asset_store = object()
    bundle_knowledge = object()
    setattr(agent, "_agent_os_asset_store", bundle_asset_store)
    setattr(agent, "_agent_os_knowledge", bundle_knowledge)
    captured: dict[str, object] = {}

    def fail_asset_store_from_settings(*args, **kwargs):
        raise AssertionError("auto recall should reuse bundled asset store")

    def fake_auto_retrieve(*args, **kwargs):
        captured["knowledge"] = kwargs["knowledge"]
        captured["asset_store"] = kwargs["asset_store"]
        return "<ordered_context><asset_references>复用实例</asset_references></ordered_context>"

    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, agent))
    monkeypatch.setattr(web, "asset_store_from_settings", fail_asset_store_from_settings)
    monkeypatch.setattr(web, "build_auto_retrieval_context", fake_auto_retrieve)
    c = TestClient(web.app)

    r = c.post(
        "/chat",
        json={
            "message": "你好",
            "client_id": "c1",
            "session_id": "s1",
            "include_trace": False,
            "use_slow_reasoning": False,
        },
    )

    assert r.status_code == 200
    assert captured["knowledge"] is bundle_knowledge
    assert captured["asset_store"] is bundle_asset_store
    assert "复用实例" in agent.messages[0]


def test_web_chat_rejects_malformed_json() -> None:
    c = TestClient(web.app)

    r = c.post("/chat", content="{bad", headers={"content-type": "application/json"})

    assert r.status_code == 422


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


def test_http_ingest_v1_rejects_invalid_target_before_bundle(monkeypatch) -> None:
    called = False

    def fail_get_bundle(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("bundle should not be built for invalid target")

    monkeypatch.setattr(web, "_get_bundle_for", fail_get_bundle)
    c = TestClient(web.app)

    r = c.post("/ingest", json={"target": "unknown", "text": "hello", "client_id": "c1"})

    assert r.status_code == 422
    assert called is False


def test_http_ingest_v1_rejects_blank_text_as_bad_request(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    settings = Settings(mem0_api_key=None, local_memory_path=local, hindsight_path=hindsight)
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, object()))
    c = TestClient(web.app)

    r = c.post("/ingest", json={"target": "mem0_profile", "text": " \n\t ", "client_id": "c1"})

    assert r.status_code == 400
    assert r.json()["detail"] == "text 不能为空"


def test_http_ingest_v1_rejects_invalid_mem_kind(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local.json"
    hindsight = tmp_path / "hindsight.jsonl"
    settings = Settings(mem0_api_key=None, local_memory_path=local, hindsight_path=hindsight)
    ctrl = MemoryController.create_default(
        mem0_api_key=None,
        mem0_host=None,
        local_memory_path=local,
        hindsight_path=hindsight,
    )
    monkeypatch.setattr(web, "_get_bundle_for", lambda *args, **kwargs: (settings, ctrl, object()))
    c = TestClient(web.app)

    r = c.post(
        "/ingest",
        json={
            "target": "mem0_profile",
            "mem_kind": "unknown_kind",
            "text": "客户偏好先给结论再给依据。",
            "client_id": "c1",
        },
    )

    assert r.status_code == 400
    assert "mem_kind" in r.json()["detail"]


def test_http_ingest_v1_rejects_oversized_text() -> None:
    c = TestClient(web.app)

    r = c.post(
        "/ingest",
        json={
            "target": "mem0_profile",
            "text": "x" * (web.INGEST_V1_MAX_TEXT_CHARS + 1),
            "client_id": "c1",
        },
    )

    assert r.status_code == 422


def test_legacy_memory_ingest_rejects_blank_text_without_500() -> None:
    c = TestClient(web.app)

    r = c.post(
        "/api/memory/ingest",
        json={"kind": "fact", "text": " \n\t ", "client_id": "c1"},
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "text 不能为空"


def test_legacy_memory_ingest_rejects_unknown_kind_before_bundle(monkeypatch) -> None:
    called = False

    def fail_get_bundle(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("bundle should not be built for invalid kind")

    monkeypatch.setattr(web, "_get_bundle_for", fail_get_bundle)
    c = TestClient(web.app)

    r = c.post("/api/memory/ingest", json={"kind": "bad", "text": "hello", "client_id": "c1"})

    assert r.status_code == 400
    assert "kind 须为" in r.json()["detail"]
    assert called is False


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


def test_web_memory_hindsight_debug_search_requires_admin(tmp_path: Path, monkeypatch) -> None:
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


def test_web_memory_hindsight_delete_invalidates_sidecar_index(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(
        web, "_get_bundle_for", lambda *args, **kwargs: (Settings(), ctrl, object())
    )
    monkeypatch.setattr(web, "AsyncReviewService", _Review)
    monkeypatch.setattr(
        web, "_transcripts", {"s1": [("user", "方向不对"), ("assistant", "我调整")]}
    )
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
