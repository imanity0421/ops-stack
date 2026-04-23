from pathlib import Path

from ops_agent.knowledge.fallback import KnowledgeJsonlFallback
from ops_agent.knowledge.graphiti_reader import GraphitiReadService
from ops_agent.knowledge.group_id import graphiti_group_id, sanitize_group_id


def test_sanitize_group_id_basic() -> None:
    assert sanitize_group_id("demo_client") == "demo_client"
    assert sanitize_group_id("a b c") == "a_b_c"


def test_graphiti_group_id_composite() -> None:
    g = graphiti_group_id("demo_client", "default_ops")
    assert g == "demo_client__default_ops"
    assert graphiti_group_id("a b", "x y") == "a_b__x_y"


def test_fallback_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "k.jsonl"
    p.write_text(
        '{"group_id": "g1", "text": "hello world ops"}\n{"group_id": "g2", "text": "other"}\n',
        encoding="utf-8",
    )
    fb = KnowledgeJsonlFallback(p)
    assert fb.enabled
    out = fb.search("ops world", "g1")
    assert "hello" in out


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
    text = svc.search_domain_knowledge("test", "any_client", skill_id="default_ops")
    assert "未配置" in text or "NEO4J" in text
