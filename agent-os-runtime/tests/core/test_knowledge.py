import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from agent_os.knowledge.graphiti_entitlements import (
    EntitlementsRevisionConflictError,
    GraphitiEntitlements,
    GraphitiEntitlementsProvider,
    append_entitlements_audit,
    graphiti_entitlements_audit_path,
    load_entitlements_file,
    save_entitlements_file,
    update_entitlements_file,
)
from agent_os.knowledge.fallback import KnowledgeJsonlFallback
from agent_os.knowledge.graphiti_ingest import _parse_reference_time, ingest_episodes_file
from agent_os.knowledge.graphiti_reader import GraphitiReadService
from agent_os.knowledge.group_id import (
    graphiti_group_id,
    sanitize_group_id,
    system_graphiti_group_id,
)


def test_sanitize_group_id_basic() -> None:
    assert sanitize_group_id("demo_client") == "demo_client"
    assert sanitize_group_id("a b c") == "a_b_c"


def test_graphiti_group_id_composite() -> None:
    g = graphiti_group_id("demo_client", "default_agent")
    assert g == "demo_client__default_agent"
    assert graphiti_group_id("a b", "x y") == "a_b__x_y"


def test_system_graphiti_group_id_ignores_client() -> None:
    assert system_graphiti_group_id("default_agent") == "default_agent"
    assert system_graphiti_group_id("short video") == "short_video"


def test_fallback_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_text(
        '{"group_id": "g1", "text": "hello world generic"}\n{"group_id": "g2", "text": "other"}\n',
        encoding="utf-8",
    )
    fb = KnowledgeJsonlFallback(p)
    assert fb.enabled
    out = fb.search("generic world", "g1")
    assert "hello" in out


def test_fallback_jsonl_skips_non_object_rows(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_text(
        '["not", "object"]\n42\n{"group_id": "g1", "text": 123}\n{"group_id": "g1", "text": "hello world"}\n',
        encoding="utf-8",
    )
    fb = KnowledgeJsonlFallback(p)
    assert fb.search("hello", "g1") == "hello world"


def test_fallback_jsonl_accepts_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_text('\ufeff{"group_id": "g1", "text": "hello bom"}\n', encoding="utf-8")
    fb = KnowledgeJsonlFallback(p)
    assert fb.search("bom", "g1") == "hello bom"


def test_fallback_jsonl_bad_utf8_is_disabled(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_bytes(b"\xff\xfe\x00")

    fb = KnowledgeJsonlFallback(p)

    assert fb.enabled is False
    assert fb.search("anything", "g1") == ""


def test_fallback_directory_path_is_disabled(tmp_path: Path) -> None:
    d = tmp_path / "k-dir"
    d.mkdir()
    fb = KnowledgeJsonlFallback(d)
    assert fb.enabled is False
    assert fb.search("anything", "g1") == ""


def test_graphiti_service_no_neo4j_uses_message(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    svc = GraphitiReadService(
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        timeout_sec=1.0,
        max_results=5,
        bfs_max_depth=2,
        fallback=KnowledgeJsonlFallback(p),
    )
    text = svc.search_domain_knowledge("test", "any_client", skill_id="default_agent")
    assert "未配置" in text or "NEO4J" in text


def test_graphiti_fallback_reads_system_group_before_legacy(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_text(
        '{"group_id": "demo_client__default_agent", "text": "legacy client knowledge"}\n'
        '{"group_id": "default_agent", "text": "system clean knowledge"}\n',
        encoding="utf-8",
    )
    svc = GraphitiReadService(
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        timeout_sec=1.0,
        max_results=5,
        bfs_max_depth=2,
        fallback=KnowledgeJsonlFallback(p),
    )

    text = svc.search_domain_knowledge("knowledge", "demo_client", skill_id="default_agent")

    assert "system clean knowledge" in text
    assert "legacy client knowledge" not in text


def test_graphiti_fallback_reads_legacy_client_group_when_system_empty(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_text(
        '{"group_id": "demo_client__default_agent", "text": "legacy client knowledge"}\n',
        encoding="utf-8",
    )
    svc = GraphitiReadService(
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        timeout_sec=1.0,
        max_results=5,
        bfs_max_depth=2,
        fallback=KnowledgeJsonlFallback(p),
    )

    text = svc.search_domain_knowledge("knowledge", "demo_client", skill_id="default_agent")

    assert "legacy client-skill group" in text
    assert "legacy client knowledge" in text


def test_graphiti_fallback_can_disable_legacy_client_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENABLE_LEGACY_CLIENT_GROUPS", "0")
    p = tmp_path / "k.jsonl"
    p.write_text(
        '{"group_id": "demo_client__default_agent", "text": "legacy client knowledge"}\n',
        encoding="utf-8",
    )
    svc = GraphitiReadService(
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        timeout_sec=1.0,
        max_results=5,
        bfs_max_depth=2,
        fallback=KnowledgeJsonlFallback(p),
    )

    text = svc.search_domain_knowledge("knowledge", "demo_client", skill_id="default_agent")

    assert "legacy client knowledge" not in text
    assert "未配置" in text or "无可用结果" in text


def test_graphiti_entitlements_file_takes_precedence_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = tmp_path / "entitlements.json"
    ep.write_text(
        json.dumps(
            {
                "version": 1,
                "global_allowed_skill_ids": ["skill_from_file"],
                "client_entitlements": {"c1": ["skill_a"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ALLOWED_SKILL_IDS", "skill_from_env")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON", '{"c1":["skill_b"]}')

    ent = GraphitiEntitlements.from_sources(path=ep)
    assert ent.allows("c1", "skill_a") is True
    assert ent.allows("c1", "skill_b") is False
    assert ent.allows("c2", "skill_from_file") is True
    assert ent.allows("c2", "skill_from_env") is False


def test_graphiti_entitlements_falls_back_to_env_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = tmp_path / "missing.json"
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ALLOWED_SKILL_IDS", "skill_x")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON", '{"c1":["skill_y"]}')
    ent = GraphitiEntitlements.from_sources(path=ep)
    assert ent.allows("c1", "skill_y") is True
    assert ent.allows("c1", "skill_x") is False
    assert ent.allows("c2", "skill_x") is True
    assert ent.allows("c2", "other") is False


def test_graphiti_entitlements_ignores_non_string_skill_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = tmp_path / "entitlements.json"
    ep.write_text(
        json.dumps(
            {
                "version": 1,
                "global_allowed_skill_ids": ["skill_a", 123],
                "client_entitlements": {"c1": ["skill_b", 456]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON", '{"c2":["skill_c",789]}')

    doc = load_entitlements_file(ep)
    ent = GraphitiEntitlements.from_sources(path=ep)

    assert doc["global_allowed_skill_ids"] == ["skill_a"]
    assert doc["client_entitlements"] == {"c1": ["skill_b"]}
    assert ent.allows("c1", "skill_b") is True
    assert ent.allows("c1", "456") is False
    assert ent.allows("c2", "123") is False


def test_graphiti_service_respects_persistent_entitlements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = tmp_path / "entitlements.json"
    ep.write_text(
        json.dumps(
            {
                "version": 1,
                "global_allowed_skill_ids": [],
                "client_entitlements": {"c_allow": ["s1"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", str(ep))
    p = tmp_path / "k.jsonl"
    p.write_text('{"group_id": "s1", "text": "allowed knowledge"}\n', encoding="utf-8")
    svc = GraphitiReadService(
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        timeout_sec=1.0,
        max_results=5,
        bfs_max_depth=2,
        fallback=KnowledgeJsonlFallback(p),
    )
    ok = svc.search_domain_knowledge("knowledge", "c_allow", skill_id="s1")
    deny = svc.search_domain_knowledge("knowledge", "c_deny", skill_id="s1")
    assert "allowed knowledge" in ok
    assert "无权访问" in deny


def test_graphiti_entitlements_audit_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ap = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH", str(ap))
    append_entitlements_audit(
        action="set_client",
        actor="tester",
        source="unit_test",
        entitlements_path=tmp_path / "ent.json",
        before={"version": 1, "global_allowed_skill_ids": [], "client_entitlements": {}},
        after={"version": 1, "global_allowed_skill_ids": [], "client_entitlements": {"c1": ["s1"]}},
        metadata={"k": "v"},
    )
    rows = [x for x in ap.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows
    assert graphiti_entitlements_audit_path() == ap


def test_graphiti_entitlements_save_is_atomic_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC", "5")
    p = tmp_path / "ent.json"

    def _writer(n: int) -> None:
        doc = load_entitlements_file(p)
        doc["client_entitlements"][f"c{n}"] = [f"s{n}"]
        save_entitlements_file(p, doc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(final, dict)
    assert isinstance(final.get("client_entitlements"), dict)


def test_graphiti_entitlements_update_with_expected_revision(tmp_path: Path) -> None:
    p = tmp_path / "ent.json"
    save_entitlements_file(
        p,
        {"version": 1, "revision": 0, "global_allowed_skill_ids": [], "client_entitlements": {}},
    )
    before, after = update_entitlements_file(
        p,
        expected_revision=0,
        mutator=lambda cur: cur.__setitem__("global_allowed_skill_ids", ["s1"]),
    )
    assert before["revision"] == 0
    assert after["revision"] == 1
    with pytest.raises(EntitlementsRevisionConflictError):
        update_entitlements_file(
            p,
            expected_revision=0,
            mutator=lambda cur: cur.__setitem__("global_allowed_skill_ids", ["s2"]),
        )


def test_graphiti_entitlements_audit_append_is_locked_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ap = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH", str(ap))
    monkeypatch.setenv("AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC", "5")

    def _append(i: int) -> None:
        append_entitlements_audit(
            action="set_client",
            actor=f"u{i}",
            source="unit_test",
            entitlements_path=tmp_path / "ent.json",
            before={"version": 1, "global_allowed_skill_ids": [], "client_entitlements": {}},
            after={
                "version": 1,
                "global_allowed_skill_ids": [],
                "client_entitlements": {f"c{i}": ["s1"]},
            },
            metadata={"idx": i},
        )

    threads = [threading.Thread(target=_append, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = [x for x in ap.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 20
    rows = [json.loads(x) for x in lines]
    assert len(rows) == 20


def test_graphiti_entitlements_audit_rotation_and_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ap = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH", str(ap))
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_BYTES", "400")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_FILES", "2")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_RETENTION_DAYS", "1")
    for i in range(30):
        append_entitlements_audit(
            action="set_client",
            actor="u",
            source="unit_test",
            entitlements_path=tmp_path / "ent.json",
            before={
                "version": 1,
                "revision": i,
                "global_allowed_skill_ids": [],
                "client_entitlements": {},
            },
            after={
                "version": 1,
                "revision": i + 1,
                "global_allowed_skill_ids": [],
                "client_entitlements": {},
            },
            metadata={"payload": "x" * 80},
        )

    # 旋转文件总数应受 max_files 控制（base + .1 + .2）
    existing = [
        p for p in [ap, ap.with_name("audit.jsonl.1"), ap.with_name("audit.jsonl.2")] if p.exists()
    ]
    assert existing
    assert len(existing) <= 3

    # 构造一个过期文件，下一次 append 后应被保留策略清理
    old = ap.with_name("audit.jsonl.2")
    old.write_text("old\n", encoding="utf-8")
    old_ts = time.time() - 10 * 86400
    os.utime(old, (old_ts, old_ts))
    append_entitlements_audit(
        action="set_global",
        actor="u",
        source="unit_test",
        entitlements_path=tmp_path / "ent.json",
        before={
            "version": 1,
            "revision": 1,
            "global_allowed_skill_ids": [],
            "client_entitlements": {},
        },
        after={
            "version": 1,
            "revision": 2,
            "global_allowed_skill_ids": ["s1"],
            "client_entitlements": {},
        },
    )
    if old.exists():
        assert old.read_text(encoding="utf-8") != "old\n"


def test_graphiti_entitlements_provider_reload_on_file_change(tmp_path: Path) -> None:
    ep = tmp_path / "ent.json"
    ep.write_text(
        json.dumps(
            {"version": 1, "global_allowed_skill_ids": [], "client_entitlements": {"c1": ["s1"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p = GraphitiEntitlementsProvider(path=ep, cache_ttl_sec=3600.0)
    assert p.get().allows("c2", "s1") is False

    time.sleep(0.01)
    ep.write_text(
        json.dumps(
            {
                "version": 1,
                "global_allowed_skill_ids": [],
                "client_entitlements": {"c1": ["s1"], "c2": ["s1"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert p.get().allows("c2", "s1") is True


def test_graphiti_service_hot_reload_entitlements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = tmp_path / "ent.json"
    ep.write_text(
        json.dumps(
            {"version": 1, "global_allowed_skill_ids": [], "client_entitlements": {"c1": ["s1"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p = tmp_path / "k.jsonl"
    p.write_text('{"group_id": "s1", "text": "knowledge s1"}\n', encoding="utf-8")
    svc = GraphitiReadService(
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        timeout_sec=1.0,
        max_results=5,
        bfs_max_depth=2,
        fallback=KnowledgeJsonlFallback(p),
        entitlements_provider=GraphitiEntitlementsProvider(path=ep, cache_ttl_sec=3600.0),
    )
    deny = svc.search_domain_knowledge("knowledge", "c2", skill_id="s1")
    assert "无权访问" in deny

    time.sleep(0.01)
    ep.write_text(
        json.dumps(
            {
                "version": 1,
                "global_allowed_skill_ids": [],
                "client_entitlements": {"c1": ["s1"], "c2": ["s1"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ok = svc.search_domain_knowledge("knowledge", "c2", skill_id="s1")
    assert "knowledge s1" in ok


def test_graphiti_ingest_bad_reference_time_falls_back() -> None:
    dt = _parse_reference_time("not-a-date")
    assert dt.tzinfo is not None


def test_graphiti_ingest_rejects_non_list_episodes_before_optional_import(tmp_path: Path) -> None:
    p = tmp_path / "episodes.json"
    p.write_text('{"episodes": null}', encoding="utf-8")

    with pytest.raises(ValueError, match="episodes 数组"):
        asyncio.run(
            ingest_episodes_file(
                p,
                neo4j_uri="bolt://example",
                neo4j_user="neo4j",
                neo4j_password="pw",
            )
        )


def test_graphiti_ingest_bad_utf8_is_value_error(tmp_path: Path) -> None:
    p = tmp_path / "episodes.json"
    p.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ValueError, match="无法读取或解析"):
        asyncio.run(
            ingest_episodes_file(
                p,
                neo4j_uri="bolt://example",
                neo4j_user="neo4j",
                neo4j_password="pw",
            )
        )
