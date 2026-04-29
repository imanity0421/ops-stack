from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

ArtifactStatus = Literal["active", "archived"]
DigestStatus = Literal["pending", "built", "failed"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).isoformat()


def new_artifact_id(now: datetime | None = None) -> str:
    stamp = (now or _utc_now()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"artifact_{stamp}_{secrets.token_hex(4)}"


def artifact_digest_fallback(raw_content: str, *, max_chars: int = 200) -> str:
    text = " ".join((raw_content or "").strip().split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    task_id: str
    session_id: str
    status: ArtifactStatus
    raw_content: str
    digest: str | None
    digest_status: DigestStatus
    created_at: str
    updated_at: str

    @property
    def ref_digest(self) -> str:
        if self.digest_status == "built" and self.digest:
            return self.digest
        return artifact_digest_fallback(self.raw_content)


def _record_from_row(row: sqlite3.Row | None) -> ArtifactRecord | None:
    if row is None:
        return None
    return ArtifactRecord(
        artifact_id=str(row["artifact_id"]),
        task_id=str(row["task_id"]),
        session_id=str(row["session_id"]),
        status=row["status"],
        raw_content=str(row["raw_content"]),
        digest=row["digest"],
        digest_status=row["digest_status"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class ArtifactStore:
    """Stage 2 v0 SQLite 原文层：保存 artifact 全文，prompt 侧只引用 ref/digest。"""

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
                CREATE TABLE IF NOT EXISTS artifacts (
                  artifact_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  raw_content TEXT NOT NULL,
                  digest TEXT,
                  digest_status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_task_status
                  ON artifacts(task_id, status, updated_at);
                """
            )

    def create_artifact(
        self,
        *,
        task_id: str,
        session_id: str,
        raw_content: str,
        digest: str | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        content = str(raw_content or "")
        if not content.strip():
            raise ValueError("raw_content must not be empty")
        aid = artifact_id or new_artifact_id()
        now = _iso()
        digest_text = digest.strip() if isinstance(digest, str) and digest.strip() else None
        digest_status: DigestStatus = "built" if digest_text else "pending"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts
                  (artifact_id, task_id, session_id, status, raw_content, digest,
                   digest_status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (aid, task_id, session_id, content, digest_text, digest_status, now, now),
            )
        return ArtifactRecord(
            artifact_id=aid,
            task_id=task_id,
            session_id=session_id,
            status="active",
            raw_content=content,
            digest=digest_text,
            digest_status=digest_status,
            created_at=now,
            updated_at=now,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _record_from_row(row)

    def list_artifacts(
        self,
        *,
        task_id: str,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[ArtifactRecord]:
        where = "task_id = ?" if include_archived else "task_id = ? AND status = 'active'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM artifacts
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (task_id, max(1, int(limit))),
            ).fetchall()
        return [_record_from_row(row) for row in rows if row is not None]

    def archive_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        now = _iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE artifacts SET status = 'archived', updated_at = ? WHERE artifact_id = ?",
                (now, artifact_id),
            )
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _record_from_row(row)
