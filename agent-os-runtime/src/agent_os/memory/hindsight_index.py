from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from agent_os.memory.hindsight_retrieval import query_features, recorded_epoch

_WS = re.compile(r"\s+")


def _norm_text(text: str) -> str:
    return _WS.sub(" ", (text or "").strip()).casefold()


def _cluster_key(row: dict[str, Any]) -> str:
    typ = str(row.get("type") or "")
    return f"{typ}\n{_norm_text(str(row.get('text') or ''))}"


def _compatible_cluster(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("type") != b.get("type"):
        return False
    for key in ("skill_id", "deliverable_type"):
        av = a.get(key)
        bv = b.get(key)
        if av and bv and av != bv:
            return False
    return True


def _feature_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(max(1, min(len(a), len(b))))


def _same_bonus(row_value: Any, requested: str | None, points: float) -> float:
    if requested and row_value == requested:
        return points
    return 0.0


@dataclass
class HindsightClusterSummary:
    key: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    features: set[str] = field(default_factory=set)
    best_recorded_epoch: float = 0.0
    representative: dict[str, Any] = field(default_factory=dict)

    def add(self, row: dict[str, Any]) -> None:
        if not self.representative:
            self.representative = row
        self.rows.append(row)
        self.features.update(query_features(str(row.get("text") or "")))
        self.best_recorded_epoch = max(self.best_recorded_epoch, recorded_epoch(row))


@dataclass(frozen=True)
class HindsightDerivedIndex:
    """运行时派生索引：只用于候选路由，不写回、不压缩、不删除 Hindsight 原始行。"""

    clusters: list[HindsightClusterSummary]

    @classmethod
    def build(cls, rows: list[dict[str, Any]]) -> HindsightDerivedIndex:
        by_key: dict[str, HindsightClusterSummary] = {}
        for row in rows:
            key = semantic_cluster_key(row, by_key.values())
            summary = by_key.setdefault(key, HindsightClusterSummary(key=key))
            summary.add(row)
        return cls(clusters=list(by_key.values()))

    def route(
        self,
        *,
        query_terms: set[str],
        user_id: str | None,
        task_id: str | None,
        skill_id: str | None,
        deliverable_type: str | None,
        max_rows: int,
    ) -> list[dict[str, Any]]:
        if max_rows <= 0:
            return []
        scored: list[tuple[float, HindsightClusterSummary]] = []
        nowish = max((c.best_recorded_epoch for c in self.clusters), default=0.0)
        for cluster in self.clusters:
            representative = cluster.representative or (cluster.rows[0] if cluster.rows else {})
            overlap = len(query_terms & cluster.features)
            score = float(overlap * 8 if query_terms else 1)
            score += _same_bonus(representative.get("user_id"), user_id, 6.0)
            score += _same_bonus(representative.get("task_id"), task_id, 8.0)
            score += _same_bonus(representative.get("skill_id"), skill_id, 4.0)
            score += _same_bonus(representative.get("deliverable_type"), deliverable_type, 3.0)
            score += min(3.0, math.log2(1 + len(cluster.rows)))
            if nowish > 0 and cluster.best_recorded_epoch >= nowish:
                score += 0.25
            scored.append((score, cluster))

        scored.sort(key=lambda x: -x[0])
        routed: list[dict[str, Any]] = []
        for _, cluster in scored:
            for row in cluster.rows:
                routed.append(row)
                if len(routed) >= max_rows:
                    return routed
        return routed


def semantic_cluster_key(
    row: dict[str, Any],
    existing: Any = (),
    *,
    min_similarity: float = 0.55,
) -> str:
    """返回确定性的近似经验簇 key。

    优先使用完全规范化文本；若已有簇与当前行在轻量特征上高度重合，则复用该簇，
    用于候选路由和预算多样化，不会合并或删除原始 Hindsight 行。
    """
    exact = _cluster_key(row)
    features = query_features(str(row.get("text") or ""))
    best_key = exact
    best_score = 0.0
    for cluster in existing:
        if not isinstance(cluster, HindsightClusterSummary):
            continue
        representative = cluster.representative or (cluster.rows[0] if cluster.rows else {})
        if not _compatible_cluster(row, representative):
            continue
        score = _feature_similarity(features, cluster.features)
        if score > best_score:
            best_score = score
            best_key = cluster.key
    if best_score >= min_similarity:
        return best_key
    return exact


def route_hindsight_candidates(
    rows: list[dict[str, Any]],
    *,
    query_terms: set[str],
    user_id: str | None,
    task_id: str | None,
    skill_id: str | None,
    deliverable_type: str | None,
    max_rows: int,
) -> list[dict[str, Any]]:
    if len(rows) <= max_rows:
        return rows
    return HindsightDerivedIndex.build(rows).route(
        query_terms=query_terms,
        user_id=user_id,
        task_id=task_id,
        skill_id=skill_id,
        deliverable_type=deliverable_type,
        max_rows=max_rows,
    )
