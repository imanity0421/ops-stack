from __future__ import annotations

from typing import Any, List, Protocol

from agent_os.memory.models import MemorySearchHit


class MemoryBackendProtocol(Protocol):
    """Mem0 与本地后端的统一接口。"""

    def mem_user_id(self, client_id: str, user_id: str | None) -> str:
        """传给 Mem0 的 user_id（含租户隔离）。"""
        ...

    def add_messages(
        self,
        *,
        messages: list[dict[str, str]],
        client_id: str,
        user_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        limit: int = 8,
    ) -> List[MemorySearchHit]: ...

    def snapshot_client_profile(self, client_id: str, user_id: str | None) -> None:
        """可选：定期快照钩子（本地后端可为空操作）。"""
        ...
