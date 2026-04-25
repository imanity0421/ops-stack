from __future__ import annotations

import logging
from typing import Any, List

from mem0.client.types import AddMemoryOptions

from agent_os.memory.models import MemorySearchHit
from agent_os.util.retry import retry_sync

logger = logging.getLogger(__name__)


class Mem0MemoryBackend:
    """Mem0 托管 API 封装；user_id 使用 client_id 与 user_id 组合以保证多租户隔离。"""

    def __init__(self, api_key: str | None, host: str | None = None) -> None:
        from mem0 import MemoryClient

        if not api_key:
            raise ValueError("Mem0MemoryBackend 需要 MEM0_API_KEY")
        self._client = MemoryClient(api_key=api_key, host=host)

    @staticmethod
    def mem_user_id(client_id: str, user_id: str | None) -> str:
        if user_id:
            return f"{client_id}::{user_id}"
        return client_id

    def add_messages(
        self,
        *,
        messages: list[dict[str, str]],
        client_id: str,
        user_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        uid = self.mem_user_id(client_id, user_id)
        options = AddMemoryOptions(filters={"user_id": uid}, metadata=metadata)

        def _add() -> dict[str, Any]:
            return self._client.add(messages, options)

        return retry_sync(_add, label="mem0.add")

    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        limit: int = 8,
    ) -> List[MemorySearchHit]:
        uid = self.mem_user_id(client_id, user_id)

        def _search() -> Any:
            return self._client.search(query, filters={"user_id": uid}, top_k=limit)

        raw = retry_sync(_search, label="mem0.search")
        hits: List[MemorySearchHit] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("results") or raw.get("memories") or []
        else:
            items = []
        if not isinstance(items, list):
            items = []
        for it in items:
            if isinstance(it, dict):
                text = it.get("memory") or it.get("text") or it.get("content") or str(it)
                meta = {k: v for k, v in it.items() if k not in ("memory", "text", "content")}
                hits.append(MemorySearchHit(text=str(text), metadata=meta))
            else:
                hits.append(MemorySearchHit(text=str(it)))
        return hits

    def snapshot_client_profile(self, client_id: str, user_id: str | None) -> None:
        logger.debug("Mem0 snapshot noop (平台侧已持久化): client_id=%s", client_id)
