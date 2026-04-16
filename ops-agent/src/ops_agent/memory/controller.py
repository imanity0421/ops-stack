from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, List, Protocol

from ops_agent.memory.backends.local import LocalMemoryBackend
from ops_agent.memory.backends.mem0 import Mem0MemoryBackend
from ops_agent.memory.hindsight_store import HindsightStore
from ops_agent.memory.models import MemoryLane, MemorySearchHit, MemoryWriteResult, UserFact

logger = logging.getLogger(__name__)


class _Backend(Protocol):
    def mem_user_id(self, client_id: str, user_id: str | None) -> str: ...
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
        limit: int,
    ) -> List[MemorySearchHit]: ...
    def snapshot_client_profile(self, client_id: str, user_id: str | None) -> None: ...


def _fingerprint(client_id: str, lane: MemoryLane, text: str) -> str:
    raw = f"{client_id}|{lane.value}|{text.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class MemoryController:
    """
    唯一推荐写入入口：Mem0（属性/偏好）与 Hindsight（任务反馈与教训）。
    Graphiti 不由本类写入。
    """

    def __init__(
        self,
        backend: _Backend,
        *,
        hindsight: HindsightStore | None,
        snapshot_every_n_turns: int = 5,
    ) -> None:
        self._backend = backend
        self._hindsight = hindsight
        self._snapshot_every_n = snapshot_every_n_turns
        self._recent_fingerprints: set[str] = set()
        self._turn_counters: dict[str, int] = {}

    @property
    def hindsight_store(self) -> HindsightStore | None:
        return self._hindsight

    @classmethod
    def create_default(
        cls,
        *,
        mem0_api_key: str | None,
        mem0_host: str | None,
        local_memory_path: Path,
        hindsight_path: Path,
        snapshot_every_n_turns: int = 5,
    ) -> MemoryController:
        if mem0_api_key:
            backend: _Backend = Mem0MemoryBackend(api_key=mem0_api_key, host=mem0_host)
            logger.info("Memory backend: Mem0 (hosted)")
        else:
            backend = LocalMemoryBackend(local_memory_path)
            logger.warning("未设置 MEM0_API_KEY，使用本地 JSON 后端: %s", local_memory_path)

        hs = HindsightStore(hindsight_path)
        return cls(backend, hindsight=hs, snapshot_every_n_turns=snapshot_every_n_turns)

    def ingest_user_fact(self, fact: UserFact) -> MemoryWriteResult:
        fp = _fingerprint(fact.client_id, fact.lane, fact.text)
        if fp in self._recent_fingerprints:
            return MemoryWriteResult(dedup_skipped=True, dedup_reason="fingerprint_duplicate")
        self._recent_fingerprints.add(fp)

        written: list[Any] = []

        if fact.lane == MemoryLane.ATTRIBUTE:
            meta = {
                "type": fact.fact_type,
                "client_id": fact.client_id,
                "source": "ops_agent.memory.controller",
            }
            messages = [
                {"role": "user", "content": fact.text},
                {"role": "assistant", "content": "已记录客户事实或偏好。"},
            ]
            self._backend.add_messages(
                messages=messages,
                client_id=fact.client_id,
                user_id=fact.user_id,
                metadata=meta,
            )
            written.append("mem0")
        elif fact.lane == MemoryLane.TASK_FEEDBACK:
            if self._hindsight is None:
                raise RuntimeError("Hindsight 未配置")
            self._hindsight.append_feedback(fact)
            written.append("hindsight")

        return MemoryWriteResult(written_to=written)

    def search_profile(self, query: str, client_id: str, user_id: str | None, limit: int = 8):
        return self._backend.search(query, client_id=client_id, user_id=user_id, limit=limit)

    def search_hindsight(self, query: str, client_id: str, limit: int = 8) -> list[str]:
        if self._hindsight is None:
            return []
        return self._hindsight.search_lessons(query, client_id, limit=limit)

    def bump_turn_and_maybe_snapshot(self, client_id: str, user_id: str | None) -> None:
        key = f"{client_id}::{user_id or ''}"
        n = self._turn_counters.get(key, 0) + 1
        self._turn_counters[key] = n
        if n > 0 and n % self._snapshot_every_n == 0:
            self._backend.snapshot_client_profile(client_id, user_id)
