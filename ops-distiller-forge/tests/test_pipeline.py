from __future__ import annotations

import json
from pathlib import Path

from ops_distiller_forge.export.manifest import read_agent_manifest, write_agent_manifest
from ops_distiller_forge.metrics.coverage import naive_recall_score
from ops_distiller_forge.ontology.models import AgentManifestV1, KnowledgePoint
from ops_distiller_forge.pipeline.episode_projector import knowledge_point_to_episode
from ops_distiller_forge.pipeline.map_stage import map_lesson_merged
from ops_distiller_forge.pipeline.reduce_stage import reduce_placeholder


def test_map_deterministic(minimal_merged: Path) -> None:
    kps = map_lesson_merged(minimal_merged, source_relpath="u1/lesson_merged.json", use_dspy=False)
    assert len(kps) == 1
    assert kps[0].theory_logic
    assert kps[0].metadata.source_relpath == "u1/lesson_merged.json"


def test_episode_projection(minimal_merged: Path) -> None:
    kps = map_lesson_merged(minimal_merged, use_dspy=False)
    ep = knowledge_point_to_episode(kps[0])
    assert "私域" in ep.body or len(ep.body) > 20
    assert ep.knowledge_point_id == kps[0].id


def test_reduce_placeholder(minimal_merged: Path) -> None:
    kps = map_lesson_merged(minimal_merged, use_dspy=False)
    out = reduce_placeholder(kps * 2)
    assert len(out) == 1


def test_naive_recall() -> None:
    assert naive_recall_score("hello 引流 获客", ["引流", "缺失词"]) == 0.5


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = AgentManifestV1(system_prompt="test", handbook_version="1.0")
    p = tmp_path / "m.json"
    write_agent_manifest(p, m)
    m2 = read_agent_manifest(p)
    assert m2.handbook_version == "1.0"
    assert m2.manifest_version == "1.0"
