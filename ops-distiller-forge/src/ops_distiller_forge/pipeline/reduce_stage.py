from __future__ import annotations

from collections import defaultdict

from ops_distiller_forge.ontology.models import KnowledgePoint


def reduce_placeholder(points: list[KnowledgePoint]) -> list[KnowledgePoint]:
    """
    Reduce（占位）：跨课聚簇与去重尚未实现时，原样返回或按 cluster_key 粗分组。

    后续：按 embedding/标题相似度合并为「知识簇」，再拼装章节。
    """
    if not points:
        return []
    # 示例：若已填 cluster_key，则每个 key 只保留一条（取最长 theory）
    by_cluster: dict[str, list[KnowledgePoint]] = defaultdict(list)
    for p in points:
        key = p.cluster_key or f"ungrouped:{p.metadata.source_relpath}"
        by_cluster[key].append(p)

    merged: list[KnowledgePoint] = []
    for _k, group in by_cluster.items():
        best = max(group, key=lambda x: len(x.theory_logic))
        merged.append(best)
    return merged
