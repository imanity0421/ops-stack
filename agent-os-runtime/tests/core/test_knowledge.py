import asyncio
from pathlib import Path

import pytest

from agent_os.knowledge.fallback import KnowledgeJsonlFallback
from agent_os.knowledge.graphiti_ingest import _parse_reference_time, ingest_episodes_file
from agent_os.knowledge.graphiti_reader import GraphitiReadService
from agent_os.knowledge.group_id import graphiti_group_id, sanitize_group_id


def test_sanitize_group_id_basic() -> None:
    assert sanitize_group_id("demo_client") == "demo_client"
    assert sanitize_group_id("a b c") == "a_b_c"


def test_graphiti_group_id_composite() -> None:
    g = graphiti_group_id("demo_client", "default_agent")
    assert g == "demo_client__default_agent"
    assert graphiti_group_id("a b", "x y") == "a_b__x_y"


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
