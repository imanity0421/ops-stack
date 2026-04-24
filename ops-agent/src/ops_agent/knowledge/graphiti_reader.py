from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path
from typing import Any

from ops_agent.knowledge.fallback import KnowledgeJsonlFallback
from ops_agent.knowledge.group_id import graphiti_group_id
from ops_agent.util.retry import retry_sync

logger = logging.getLogger(__name__)


def _run_async(coro: Any) -> Any:
    """在同步工具内安全运行协程（避免与已有事件循环冲突）。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _build_search_config(limit: int, bfs_max_depth: int) -> Any:
    from graphiti_core.search.search_config import SearchConfig
    from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

    base = COMBINED_HYBRID_SEARCH_RRF
    edge = (
        base.edge_config.model_copy(update={"bfs_max_depth": bfs_max_depth})
        if base.edge_config
        else None
    )
    node = (
        base.node_config.model_copy(update={"bfs_max_depth": bfs_max_depth})
        if base.node_config
        else None
    )
    comm = (
        base.community_config.model_copy(update={"bfs_max_depth": bfs_max_depth})
        if base.community_config
        else None
    )
    ep = base.episode_config

    return SearchConfig(
        edge_config=edge,
        node_config=node,
        episode_config=ep,
        community_config=comm,
        limit=limit,
    )


def _format_results(results: Any) -> str:
    parts: list[str] = []
    for e in results.edges:
        line = getattr(e, "fact", None) or str(e)
        if line:
            parts.append(f"[边] {line}")
    for n in results.nodes:
        name = getattr(n, "name", "") or ""
        summary = getattr(n, "summary", "") or ""
        if name or summary:
            parts.append(f"[实体] {name}: {summary}".strip())
    for ep in results.episodes:
        content = getattr(ep, "content", None) or getattr(ep, "name", None) or ""
        if content:
            parts.append(f"[片段] {content}")
    for c in results.communities:
        name = getattr(c, "name", "") or ""
        summary = getattr(c, "summary", "") or ""
        if name or summary:
            parts.append(f"[社区] {name}: {summary}".strip())
    if not parts:
        return ""
    return "\n".join(parts[:50])


class GraphitiReadService:
    """
    Graphiti 只读检索：仅调用 search/search_，不调用 add_episode。
    生产环境建议在 Neo4j 侧使用只读账号。
    """

    def __init__(
        self,
        *,
        neo4j_uri: str | None,
        neo4j_user: str | None,
        neo4j_password: str | None,
        timeout_sec: float,
        max_results: int,
        bfs_max_depth: int,
        fallback: KnowledgeJsonlFallback,
    ) -> None:
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._timeout_sec = timeout_sec
        self._max_results = max_results
        self._bfs_max_depth = bfs_max_depth
        self._fallback = fallback
        self._graphiti: Any = None

    @classmethod
    def from_env(cls, fallback_path: Path | None) -> GraphitiReadService:
        return cls(
            neo4j_uri=os.getenv("NEO4J_URI"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD"),
            timeout_sec=float(os.getenv("OPS_GRAPHITI_SEARCH_TIMEOUT_SEC", "20")),
            max_results=int(os.getenv("OPS_GRAPHITI_MAX_RESULTS", "12")),
            bfs_max_depth=int(os.getenv("OPS_GRAPHITI_BFS_MAX_DEPTH", "2")),
            fallback=KnowledgeJsonlFallback(fallback_path),
        )

    def is_graphiti_configured(self) -> bool:
        return bool(self._neo4j_uri and self._neo4j_password)

    def _lazy_graphiti(self) -> Any:
        if self._graphiti is not None:
            return self._graphiti
        try:
            from graphiti_core.graphiti import Graphiti
        except ImportError as e:
            raise RuntimeError('未安装 graphiti-core，请执行: pip install -e ".[graphiti]"') from e
        assert self._neo4j_uri and self._neo4j_password
        self._graphiti = Graphiti(
            uri=self._neo4j_uri,
            user=self._neo4j_user or "neo4j",
            password=self._neo4j_password,
        )
        return self._graphiti

    async def _search_graphiti(self, query: str, group_id: str) -> str:
        g = self._lazy_graphiti()
        cfg = _build_search_config(self._max_results, self._bfs_max_depth)
        results = await asyncio.wait_for(
            g.search_(query, config=cfg, group_ids=[group_id]),
            timeout=self._timeout_sec,
        )
        return _format_results(results)

    def search_domain_knowledge(self, query: str, client_id: str, skill_id: str) -> str:
        """
        同步入口：按 ``graphiti_group_id(client_id, skill_id)`` 查 Graphiti；失败或超时则走 JSONL fallback。
        """
        gid = graphiti_group_id(client_id, skill_id)
        notes: list[str] = []

        if self.is_graphiti_configured():
            try:

                def _graph_search() -> str:
                    return _run_async(self._search_graphiti(query, gid))

                text = retry_sync(_graph_search, attempts=3, label="graphiti.search_")
                if text.strip():
                    return text
                notes.append("Graphiti 返回空结果。")
            except Exception as e:
                logger.warning("Graphiti 检索失败，将尝试 fallback: %s", e)
                notes.append(f"Graphiti 不可用: {type(e).__name__}")

        if self._fallback.enabled:
            fb = self._fallback.search(query, gid, limit=self._max_results)
            if fb.strip():
                header = ""
                if notes:
                    header = "[降级] " + " ".join(notes) + "\n\n"
                return header + fb

        if notes:
            return "（领域知识无可用结果） " + " ".join(notes)

        return "（未配置领域知识检索：请设置 NEO4J_URI/NEO4J_PASSWORD 或提供 OPS_KNOWLEDGE_FALLBACK_PATH JSONL）"
