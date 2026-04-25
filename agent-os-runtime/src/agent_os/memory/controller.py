from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, List, Protocol

from agent_os.memory.backends.local import LocalMemoryBackend
from agent_os.memory.backends.mem0 import Mem0MemoryBackend
from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.models import MemoryLane, MemorySearchHit, MemoryWriteResult, UserFact
from agent_os.memory.policy import evaluate_memory_write

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


def _fingerprint(client_id: str, user_id: str | None, lane: MemoryLane, text: str) -> str:
    raw = f"{client_id}|{user_id or ''}|{lane.value}|{text.strip().lower()}"
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
        enable_memory_policy: bool = True,
        memory_policy_mode: str = "reject",
    ) -> None:
        self._backend = backend
        self._hindsight = hindsight
        self._snapshot_every_n = snapshot_every_n_turns
        self._enable_memory_policy = enable_memory_policy
        self._memory_policy_mode = memory_policy_mode
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
        enable_hindsight: bool = True,
        snapshot_every_n_turns: int = 5,
        enable_memory_policy: bool = True,
        memory_policy_mode: str = "reject",
    ) -> MemoryController:
        if mem0_api_key:
            backend: _Backend = Mem0MemoryBackend(api_key=mem0_api_key, host=mem0_host)
            logger.info("Memory backend: Mem0 (hosted)")
        else:
            backend = LocalMemoryBackend(local_memory_path)
            logger.warning("未设置 MEM0_API_KEY，使用本地 JSON 后端: %s", local_memory_path)

        hs = HindsightStore(hindsight_path) if enable_hindsight else None
        return cls(
            backend,
            hindsight=hs,
            snapshot_every_n_turns=snapshot_every_n_turns,
            enable_memory_policy=enable_memory_policy,
            memory_policy_mode=memory_policy_mode,
        )

    def ingest_user_fact(self, fact: UserFact) -> MemoryWriteResult:
        if self._enable_memory_policy:
            decision = evaluate_memory_write(fact)
            if not decision.allow:
                logger.info(
                    "MemoryPolicy rejected client_id=%s lane=%s reason=%s",
                    fact.client_id,
                    fact.lane.value,
                    decision.reason,
                )
                if self._memory_policy_mode == "reject":
                    return MemoryWriteResult(
                        dedup_skipped=True,
                        dedup_reason=f"policy_rejected:{decision.reason}",
                        policy_rejected=True,
                        policy_reason=decision.reason,
                    )

        fp = _fingerprint(fact.client_id, fact.user_id, fact.lane, fact.text)
        if fp in self._recent_fingerprints:
            return MemoryWriteResult(dedup_skipped=True, dedup_reason="fingerprint_duplicate")

        written: list[Any] = []

        if fact.lane == MemoryLane.ATTRIBUTE:
            meta = {
                "type": fact.fact_type,
                "client_id": fact.client_id,
                "source": "agent_os.memory.controller",
                "recorded_at": fact.recorded_at.isoformat(),
                "effective_at": fact.effective_at.isoformat() if fact.effective_at else None,
                "expires_at": fact.expires_at.isoformat() if fact.expires_at else None,
                "memory_source": fact.source,
                "confidence": fact.confidence,
            }
            messages = [
                {"role": "user", "content": fact.text},
                {"role": "assistant", "content": "已记录长期事实或偏好。"},
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

        if written:
            self._recent_fingerprints.add(fp)
        return MemoryWriteResult(written_to=written)

    def search_profile(self, query: str, client_id: str, user_id: str | None, limit: int = 8):
        return self._backend.search(query, client_id=client_id, user_id=user_id, limit=limit)

    def search_hindsight(
        self,
        query: str,
        client_id: str,
        limit: int = 8,
        *,
        temporal_grounding: bool = True,
    ) -> list[str]:
        if self._hindsight is None:
            return []
        return self._hindsight.search_lessons(
            query, client_id, limit=limit, temporal_grounding=temporal_grounding
        )

    def bump_turn_and_maybe_snapshot(self, client_id: str, user_id: str | None) -> None:
        key = f"{client_id}::{user_id or ''}"
        n = self._turn_counters.get(key, 0) + 1
        self._turn_counters[key] = n
        if self._snapshot_every_n <= 0:
            return
        if n > 0 and n % self._snapshot_every_n == 0:
            self._backend.snapshot_client_profile(client_id, user_id)
