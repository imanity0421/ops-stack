from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agent_os.memory.models import MemoryLane, MemoryScope

LedgerStatus = Literal["pending", "committed", "duplicate", "rejected", "failed"]
MemoryTarget = Literal["mem0", "hindsight"]

_WS = re.compile(r"\s+")


def normalize_memory_text(text: str) -> str:
    """用于持久化幂等的最小规范化：折叠空白，不改写中文标点与语义。"""
    return _WS.sub(" ", (text or "").strip()).casefold()


def canonical_memory_hash(
    *,
    client_id: str,
    user_id: str | None,
    scope: MemoryScope,
    lane: MemoryLane,
    text: str,
) -> tuple[str, str]:
    text_norm = normalize_memory_text(text)
    raw = f"{client_id}\x00{user_id or ''}\x00{scope}\x00{lane.value}\x00{text_norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), text_norm


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LedgerBeginResult:
    ledger_id: str | None
    duplicate: bool
    reason: str | None = None


class MemoryLedger:
    """轻量写入账本：为 MemoryController 提供跨进程幂等与基础审计。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_write_ledger (
                  ledger_id TEXT PRIMARY KEY,
                  client_id TEXT NOT NULL,
                  user_id TEXT,
                  user_key TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  lane TEXT NOT NULL,
                  target TEXT NOT NULL,
                  canonical_hash TEXT NOT NULL,
                  idempotency_key TEXT,
                  text_norm TEXT NOT NULL,
                  source TEXT NOT NULL,
                  status TEXT NOT NULL,
                  policy_reason TEXT,
                  storage_ref TEXT,
                  recorded_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(client_id, user_key, scope, lane, canonical_hash)
                );

                CREATE TABLE IF NOT EXISTS memory_write_attempts (
                  attempt_id TEXT PRIMARY KEY,
                  ledger_id TEXT,
                  client_id TEXT NOT NULL,
                  user_id TEXT,
                  user_key TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  lane TEXT NOT NULL,
                  target TEXT NOT NULL,
                  idempotency_key TEXT,
                  canonical_hash TEXT NOT NULL,
                  attempt_status TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  recorded_at TEXT NOT NULL
                );
                """
            )

    def _record_attempt(
        self,
        conn: sqlite3.Connection,
        *,
        ledger_id: str | None,
        client_id: str,
        user_id: str | None,
        user_key: str,
        scope: MemoryScope,
        lane: MemoryLane,
        target: MemoryTarget,
        idempotency_key: str | None,
        canonical_hash: str,
        attempt_status: str,
        reason: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_write_attempts
              (attempt_id, ledger_id, client_id, user_id, user_key, scope, lane, target,
               idempotency_key, canonical_hash, attempt_status, reason, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"mla_{uuid4().hex}",
                ledger_id,
                client_id,
                user_id,
                user_key,
                scope,
                lane.value,
                target,
                idempotency_key,
                canonical_hash,
                attempt_status,
                reason,
                now,
            ),
        )

    def begin_write(
        self,
        *,
        client_id: str,
        user_id: str | None,
        scope: MemoryScope,
        lane: MemoryLane,
        target: MemoryTarget,
        text: str,
        source: str,
        idempotency_key: str | None = None,
        policy_reason: str | None = None,
    ) -> LedgerBeginResult:
        canonical_hash, text_norm = canonical_memory_hash(
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            lane=lane,
            text=text,
        )
        now = _utc_iso()
        ledger_id = f"mlg_{uuid4().hex}"
        user_key = user_id or ""
        with self._connect() as conn:
            if idempotency_key:
                row = conn.execute(
                    """
                    SELECT ledger_id, status FROM memory_write_ledger
                    WHERE client_id = ? AND user_key = ? AND scope = ? AND lane = ?
                      AND idempotency_key = ?
                    """,
                    (client_id, user_key, scope, lane.value, idempotency_key),
                ).fetchone()
                if row is not None:
                    status = str(row["status"])
                    existing_id = str(row["ledger_id"])
                    if status in ("failed", "rejected", "pending"):
                        conn.execute(
                            """
                            UPDATE memory_write_ledger
                            SET target = ?, source = ?, canonical_hash = ?, text_norm = ?,
                                status = 'pending', policy_reason = ?, storage_ref = NULL,
                                updated_at = ?
                            WHERE ledger_id = ?
                            """,
                            (
                                target,
                                source,
                                canonical_hash,
                                text_norm,
                                policy_reason,
                                now,
                                existing_id,
                            ),
                        )
                        return LedgerBeginResult(ledger_id=existing_id, duplicate=False)
                    reason = f"ledger_idempotency_{status}_duplicate"
                    self._record_attempt(
                        conn,
                        ledger_id=existing_id,
                        client_id=client_id,
                        user_id=user_id,
                        user_key=user_key,
                        scope=scope,
                        lane=lane,
                        target=target,
                        idempotency_key=idempotency_key,
                        canonical_hash=canonical_hash,
                        attempt_status="duplicate",
                        reason=reason,
                        now=now,
                    )
                    return LedgerBeginResult(
                        ledger_id=None,
                        duplicate=True,
                        reason=reason,
                    )
            try:
                conn.execute(
                    """
                    INSERT INTO memory_write_ledger
                      (ledger_id, client_id, user_id, user_key, scope, lane, target, canonical_hash,
                       idempotency_key, text_norm, source, status, policy_reason, recorded_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        ledger_id,
                        client_id,
                        user_id,
                        user_key,
                        scope,
                        lane.value,
                        target,
                        canonical_hash,
                        idempotency_key,
                        text_norm,
                        source,
                        policy_reason,
                        now,
                        now,
                    ),
                )
                return LedgerBeginResult(ledger_id=ledger_id, duplicate=False)
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """
                    SELECT ledger_id, status FROM memory_write_ledger
                    WHERE client_id = ? AND user_key = ? AND scope = ? AND lane = ? AND canonical_hash = ?
                    """,
                    (client_id, user_key, scope, lane.value, canonical_hash),
                ).fetchone()
                if row is None:
                    return LedgerBeginResult(
                        ledger_id=None, duplicate=True, reason="ledger_integrity_conflict"
                    )
                status = str(row["status"])
                existing_id = str(row["ledger_id"])
                if status in ("failed", "rejected", "pending"):
                    conn.execute(
                        """
                        UPDATE memory_write_ledger
                        SET target = ?, source = ?, status = 'pending', policy_reason = ?,
                            storage_ref = NULL, updated_at = ?
                        WHERE ledger_id = ?
                        """,
                        (target, source, policy_reason, now, existing_id),
                    )
                    return LedgerBeginResult(ledger_id=existing_id, duplicate=False)
                reason = f"ledger_{status}_duplicate"
                self._record_attempt(
                    conn,
                    ledger_id=existing_id,
                    client_id=client_id,
                    user_id=user_id,
                    user_key=user_key,
                    scope=scope,
                    lane=lane,
                    target=target,
                    idempotency_key=idempotency_key,
                    canonical_hash=canonical_hash,
                    attempt_status="duplicate",
                    reason=reason,
                    now=now,
                )
                return LedgerBeginResult(ledger_id=None, duplicate=True, reason=reason)

    def record_rejected(
        self,
        *,
        client_id: str,
        user_id: str | None,
        scope: MemoryScope,
        lane: MemoryLane,
        target: MemoryTarget,
        text: str,
        source: str,
        policy_reason: str,
        idempotency_key: str | None = None,
    ) -> None:
        begin = self.begin_write(
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            lane=lane,
            target=target,
            text=text,
            source=source,
            idempotency_key=idempotency_key,
        )
        if begin.ledger_id is None:
            return
        now = _utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_write_ledger
                SET status = 'rejected', policy_reason = ?, updated_at = ?
                WHERE ledger_id = ?
                """,
                (policy_reason, now, begin.ledger_id),
            )

    def mark_committed(self, ledger_id: str, *, storage_ref: str | None = None) -> None:
        now = _utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_write_ledger
                SET status = 'committed', storage_ref = ?, updated_at = ?
                WHERE ledger_id = ?
                """,
                (storage_ref, now, ledger_id),
            )

    def mark_failed(self, ledger_id: str, *, reason: str) -> None:
        now = _utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_write_ledger
                SET status = 'failed', policy_reason = ?, updated_at = ?
                WHERE ledger_id = ?
                """,
                (reason, now, ledger_id),
            )
