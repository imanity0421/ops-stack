from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Protocol

if TYPE_CHECKING:
    from agent_os.memory.ordered_context import RetrieveOrderedContextOptions

from agent_os.memory.backends.local import LocalMemoryBackend
from agent_os.memory.backends.mem0 import Mem0MemoryBackend
from agent_os.memory.hindsight_store import HindsightStore
from agent_os.memory.models import (
    CLIENT_SHARED_USER_ID,
    MemoryLane,
    MemoryScope,
    MemorySearchHit,
    MemoryWriteResult,
    UserFact,
)
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


def _fingerprint(
    client_id: str,
    user_id: str | None,
    scope: MemoryScope | None,
    lane: MemoryLane,
    text: str,
) -> str:
    raw = f"{client_id}|{user_id or ''}|{scope or ''}|{lane.value}|{text.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _infer_scope(fact: UserFact) -> MemoryScope:
    if fact.scope is not None:
        return fact.scope
    if fact.lane == MemoryLane.TASK_FEEDBACK:
        return "task_scoped"
    return "user_private" if fact.user_id else "client_shared"


def _profile_write_user_id(fact: UserFact, scope: MemoryScope) -> str | None:
    if scope == "client_shared":
        return CLIENT_SHARED_USER_ID
    if scope == "user_private":
        return fact.user_id
    return fact.user_id


def _profile_search_user_ids(user_id: str | None) -> list[str | None]:
    if user_id:
        return [CLIENT_SHARED_USER_ID, user_id]
    # 兼容旧数据：历史上 user_id=None 会落入 client_id 这个 legacy bucket。
    return [CLIENT_SHARED_USER_ID, None]


def _hit_recorded_epoch(hit: MemorySearchHit) -> float:
    """用于跨桶合并：无时间元数据时视为 0（旧数据仍参与去重，保留先出现的命中）。"""
    meta = hit.metadata or {}
    raw = meta.get("recorded_at") or meta.get("created_at")
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return 0.0
    s = raw.strip()
    if not s:
        return 0.0
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


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

        scope = _infer_scope(fact)
        write_user_id = _profile_write_user_id(fact, scope)
        fp = _fingerprint(fact.client_id, write_user_id, scope, fact.lane, fact.text)
        if fp in self._recent_fingerprints:
            return MemoryWriteResult(dedup_skipped=True, dedup_reason="fingerprint_duplicate")

        written: list[Any] = []

        if fact.lane == MemoryLane.ATTRIBUTE:
            meta = {
                "memory_version": "2.0",
                "scope": scope,
                "type": fact.fact_type,
                "client_id": fact.client_id,
                "user_id": write_user_id,
                "skill_id": fact.skill_id,
                "status": "active",
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
                user_id=write_user_id,
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
        """
        合并 ``client_shared``、``user_private``（及 legacy 桶）的检索结果。

        同一正文（忽略大小写/首尾空白）在多个桶重复出现时，保留 ``recorded_at`` / ``created_at``
        更晚的命中，以落实「冲突时优先较新记录」（与 Temporal Grounding 一致）。
        """
        per_scope_limit = max(limit, 1)
        merged: dict[str, MemorySearchHit] = {}
        bucket_orders: list[list[str]] = []

        for uid in _profile_search_user_ids(user_id):
            bucket_order: list[str] = []
            for hit in self._backend.search(
                query,
                client_id=client_id,
                user_id=uid,
                limit=per_scope_limit,
            ):
                key = hit.text.strip().lower()
                if not key:
                    continue
                prev = merged.get(key)
                if prev is None:
                    merged[key] = hit
                    bucket_order.append(key)
                elif _hit_recorded_epoch(hit) > _hit_recorded_epoch(prev):
                    merged[key] = hit
                elif key not in bucket_order:
                    bucket_order.append(key)
            bucket_orders.append(bucket_order)

        ordered: list[str] = []
        seen: set[str] = set()
        max_bucket_len = max((len(x) for x in bucket_orders), default=0)
        for i in range(max_bucket_len):
            for bucket_order in bucket_orders:
                if i >= len(bucket_order):
                    continue
                key = bucket_order[i]
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(key)
                if len(ordered) >= limit:
                    return [merged[k] for k in ordered]

        return [merged[k] for k in ordered[:limit]]

    def search_hindsight(
        self,
        query: str,
        client_id: str,
        limit: int = 8,
        *,
        user_id: str | None = None,
        task_id: str | None = None,
        skill_id: str | None = None,
        deliverable_type: str | None = None,
        temporal_grounding: bool = True,
    ) -> list[str]:
        if self._hindsight is None:
            return []
        return self._hindsight.search_lessons(
            query,
            client_id,
            limit=limit,
            user_id=user_id,
            task_id=task_id,
            skill_id=skill_id,
            deliverable_type=deliverable_type,
            temporal_grounding=temporal_grounding,
        )

    def retrieve_ordered_context(self, query: str, options: RetrieveOrderedContextOptions) -> str:
        """Mem0 → Hindsight → Graphiti → Asset 四层编排检索（Markdown）。"""
        from agent_os.memory.ordered_context import render_retrieve_ordered_context_markdown

        return render_retrieve_ordered_context_markdown(self, query, options)

    def bump_turn_and_maybe_snapshot(self, client_id: str, user_id: str | None) -> None:
        key = f"{client_id}::{user_id or ''}"
        n = self._turn_counters.get(key, 0) + 1
        self._turn_counters[key] = n
        if self._snapshot_every_n <= 0:
            return
        if n > 0 and n % self._snapshot_every_n == 0:
            self._backend.snapshot_client_profile(client_id, user_id)
