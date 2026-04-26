from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path
from typing import Any

from agent_os.knowledge.graphiti_entitlements import (
    GraphitiEntitlements,
    GraphitiEntitlementsProvider,
)
from agent_os.knowledge.fallback import KnowledgeJsonlFallback
from agent_os.knowledge.group_id import graphiti_group_id, system_graphiti_group_id
from agent_os.util.retry import retry_sync

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r 不是合法整数，使用默认值 %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning("%s=%r 小于最小值 %s，使用默认值 %s", name, raw, min_value, default)
        return default
    return value


def _env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("%s=%r 不是合法数字，使用默认值 %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning("%s=%r 小于最小值 %s，使用默认值 %s", name, raw, min_value, default)
        return default
    return value


def _run_async(coro: Any) -> Any:
    """在同步工具内安全运行协程（避免与已有事件循环冲突）。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _legacy_client_groups_enabled() -> bool:
    return os.getenv("AGENT_OS_GRAPHITI_ENABLE_LEGACY_CLIENT_GROUPS", "1").lower() not in (
        "0",
        "false",
        "no",
    )


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
        entitlements: GraphitiEntitlements | None = None,
        entitlements_provider: GraphitiEntitlementsProvider | None = None,
    ) -> None:
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._timeout_sec = timeout_sec
        self._max_results = max_results
        self._bfs_max_depth = bfs_max_depth
        self._fallback = fallback
        self._entitlements_static = entitlements
        self._entitlements_provider = (
            entitlements_provider
            if entitlements_provider is not None
            else GraphitiEntitlementsProvider(
                cache_ttl_sec=_env_float(
                    "AGENT_OS_GRAPHITI_ENTITLEMENTS_CACHE_TTL_SEC", 2.0, min_value=0.0
                )
            )
        )
        self._graphiti: Any = None

    @classmethod
    def from_env(cls, fallback_path: Path | None) -> GraphitiReadService:
        return cls(
            neo4j_uri=os.getenv("NEO4J_URI"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD"),
            timeout_sec=_env_float("AGENT_OS_GRAPHITI_SEARCH_TIMEOUT_SEC", 20.0, min_value=0.1),
            max_results=_env_int("AGENT_OS_GRAPHITI_MAX_RESULTS", 12, min_value=1),
            bfs_max_depth=_env_int("AGENT_OS_GRAPHITI_BFS_MAX_DEPTH", 2, min_value=0),
            fallback=KnowledgeJsonlFallback(fallback_path),
            entitlements_provider=GraphitiEntitlementsProvider(
                cache_ttl_sec=_env_float(
                    "AGENT_OS_GRAPHITI_ENTITLEMENTS_CACHE_TTL_SEC", 2.0, min_value=0.0
                )
            ),
        )

    def invalidate_entitlements_cache(self) -> None:
        if self._entitlements_provider is not None:
            self._entitlements_provider.invalidate()

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
        同步入口：按系统级 ``system_graphiti_group_id(skill_id)`` 查 Graphiti；
        ``client_id`` 仅用于权限过滤；失败或超时则走 JSONL fallback。
        """
        ent = self._entitlements_static or self._entitlements_provider.get()
        if not ent.allows(client_id, skill_id):
            return f"（当前 client_id={client_id} 无权访问 skill/domain={skill_id} 的系统知识）"
        gid = system_graphiti_group_id(skill_id)
        legacy_gid = graphiti_group_id(client_id, skill_id)
        notes: list[str] = []

        if self.is_graphiti_configured():
            try:

                def _graph_search() -> str:
                    return _run_async(self._search_graphiti(query, gid))

                text = retry_sync(_graph_search, attempts=3, label="graphiti.search_")
                if text.strip():
                    return text
                notes.append("Graphiti 返回空结果。")
                if _legacy_client_groups_enabled() and legacy_gid != gid:

                    def _legacy_graph_search() -> str:
                        return _run_async(self._search_graphiti(query, legacy_gid))

                    legacy_text = retry_sync(
                        _legacy_graph_search,
                        attempts=2,
                        label="graphiti.search_.legacy_client_group",
                    )
                    if legacy_text.strip():
                        return "[legacy client-skill group]\n" + legacy_text
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
            if _legacy_client_groups_enabled() and legacy_gid != gid:
                legacy_fb = self._fallback.search(query, legacy_gid, limit=self._max_results)
                if legacy_fb.strip():
                    header = "[legacy client-skill group]\n"
                    if notes:
                        header = "[降级] " + " ".join(notes) + "\n\n" + header
                    return header + legacy_fb

        if notes:
            return "（领域知识无可用结果） " + " ".join(notes)

        return "（未配置领域知识检索：请设置 NEO4J_URI/NEO4J_PASSWORD 或提供 AGENT_OS_KNOWLEDGE_FALLBACK_PATH JSONL）"
