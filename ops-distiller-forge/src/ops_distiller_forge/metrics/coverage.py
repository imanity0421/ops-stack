from __future__ import annotations

import re
from typing import Iterable


def coverage_keyword_hits(haystack: str, needles: Iterable[str]) -> tuple[int, int]:
    """
    粗粒度覆盖：每个 needle 是否在 haystack 中出现（子串，忽略大小写）。
    返回 (命中数, 总数)。后续可换 embedding + LLM judge。
    """
    h = haystack.lower()
    needles = list(needles)
    hit = 0
    for n in needles:
        if not n.strip():
            continue
        if n.lower() in h:
            hit += 1
    total = len([n for n in needles if n.strip()])
    return hit, total


def naive_recall_score(haystack: str, ground_truth_phrases: list[str]) -> float:
    hit, total = coverage_keyword_hits(haystack, ground_truth_phrases)
    if total == 0:
        return 1.0
    return hit / total


def token_overlap_score(a: str, b: str) -> float:
    """简易 Jaccard：用于后续 embedding 前的 baseline。"""
    ta = set(re.findall(r"[\w\u4e00-\u9fff]+", a.lower()))
    tb = set(re.findall(r"[\w\u4e00-\u9fff]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
