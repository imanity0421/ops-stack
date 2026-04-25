from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

TaskStatus = Literal["active", "inactive", "closed", "archived"]
BoundaryStatus = Literal["open", "confirmed", "dismissed", "expired"]
MessageRole = Literal["user", "assistant", "system"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).isoformat()


def new_task_id(now: datetime | None = None) -> str:
    stamp = (now or _utc_now()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"task_{stamp}_{secrets.token_hex(4)}"


def fallback_task_title(message: str, *, max_chars: int = 24) -> str:
    text = " ".join((message or "").strip().split())
    if not text:
        return "未命名任务"
    return text[:max_chars]


@dataclass(frozen=True)
class TaskSegment:
    task_id: str
    session_id: str
    client_id: str
    user_id: str | None
    primary_skill_id: str
    task_title: str
    status: TaskStatus
    created_at: str
    updated_at: str
    boundary_start_message_id: str | None = None
    boundary_reason: str | None = None
    invoked_skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskSummary:
    session_id: str
    task_id: str
    summary_text: str
    summary_version: int
    covered_message_count: int
    updated_at: str
    summary_model: str = "manual"
    summary_policy_version: str = "task_summary_v1"
    status: Literal["working", "final", "stale"] = "working"
    covered_message_start_id: str | None = None
    covered_message_end_id: str | None = None


class TaskMemoryStore:
    """同一 session 内的 task working memory 存储；不承载跨 session 长期记忆。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  session_id TEXT PRIMARY KEY,
                  client_id TEXT NOT NULL,
                  user_id TEXT,
                  active_task_id TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_segments (
                  task_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  client_id TEXT NOT NULL,
                  user_id TEXT,
                  primary_skill_id TEXT NOT NULL,
                  invoked_skills_json TEXT NOT NULL,
                  task_title TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  closed_at TEXT,
                  boundary_start_message_id TEXT,
                  boundary_end_message_id TEXT,
                  boundary_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_task_segments_session
                  ON task_segments(session_id, updated_at);

                CREATE TABLE IF NOT EXISTS session_messages (
                  message_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  sequence_no INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_session_messages_task
                  ON session_messages(session_id, task_id, sequence_no);

                CREATE TABLE IF NOT EXISTS boundary_candidates (
                  candidate_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  old_task_id TEXT NOT NULL,
                  boundary_message_id TEXT NOT NULL,
                  candidate_task_title TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  signals_json TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  ttl_message_count INTEGER NOT NULL,
                  ttl_minutes INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS boundary_events (
                  event_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  old_task_id TEXT,
                  new_task_id TEXT,
                  boundary_message_id TEXT,
                  confirmed_at_message_id TEXT,
                  reassigned_message_ids_json TEXT NOT NULL,
                  reason TEXT,
                  confidence REAL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_summaries (
                  session_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  summary_text TEXT NOT NULL,
                  summary_version INTEGER NOT NULL,
                  covered_message_start_id TEXT,
                  covered_message_end_id TEXT,
                  covered_message_count INTEGER NOT NULL,
                  updated_at TEXT NOT NULL,
                  summary_model TEXT NOT NULL,
                  summary_policy_version TEXT NOT NULL,
                  status TEXT NOT NULL,
                  PRIMARY KEY(session_id, task_id)
                );
                """
            )

    def get_or_create_active_task(
        self,
        *,
        session_id: str,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        seed_message: str = "",
    ) -> TaskSegment:
        with self._connect() as conn:
            sess = conn.execute(
                "SELECT active_task_id FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if sess and sess["active_task_id"]:
                task = self._get_task(conn, str(sess["active_task_id"]))
                if task is not None:
                    return task

            task_id = new_task_id()
            now = _iso()
            title = fallback_task_title(seed_message)
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                  (session_id, client_id, user_id, active_task_id, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM sessions WHERE session_id = ?), ?), ?, 'active')
                """,
                (session_id, client_id, user_id, task_id, session_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO task_segments
                  (task_id, session_id, client_id, user_id, primary_skill_id, invoked_skills_json,
                   task_title, status, created_at, updated_at, boundary_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    task_id,
                    session_id,
                    client_id,
                    user_id,
                    skill_id,
                    json.dumps([skill_id], ensure_ascii=False),
                    title,
                    now,
                    now,
                    "session_start",
                ),
            )
            return self._get_task(conn, task_id)  # type: ignore[return-value]

    def _get_task(self, conn: sqlite3.Connection, task_id: str) -> TaskSegment | None:
        row = conn.execute("SELECT * FROM task_segments WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return TaskSegment(
            task_id=str(row["task_id"]),
            session_id=str(row["session_id"]),
            client_id=str(row["client_id"]),
            user_id=row["user_id"],
            primary_skill_id=str(row["primary_skill_id"]),
            invoked_skills=list(json.loads(row["invoked_skills_json"] or "[]")),
            task_title=str(row["task_title"]),
            status=row["status"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            boundary_start_message_id=row["boundary_start_message_id"],
            boundary_reason=row["boundary_reason"],
        )

    def append_message(
        self,
        *,
        session_id: str,
        task_id: str,
        role: MessageRole,
        content: str,
    ) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_seq FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(row["next_seq"])
            message_id = f"msg_{seq:06d}_{secrets.token_hex(3)}"
            conn.execute(
                """
                INSERT INTO session_messages
                  (message_id, session_id, task_id, role, content, created_at, sequence_no)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, task_id, role, content, _iso(), seq),
            )
            conn.execute(
                "UPDATE task_segments SET updated_at = ? WHERE task_id = ?",
                (_iso(), task_id),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ?, active_task_id = ? WHERE session_id = ?",
                (_iso(), task_id, session_id),
            )
            return message_id

    def upsert_summary(self, summary: TaskSummary) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_summaries
                  (session_id, task_id, summary_text, summary_version, covered_message_start_id,
                   covered_message_end_id, covered_message_count, updated_at, summary_model,
                   summary_policy_version, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, task_id) DO UPDATE SET
                  summary_text = excluded.summary_text,
                  summary_version = excluded.summary_version,
                  covered_message_start_id = excluded.covered_message_start_id,
                  covered_message_end_id = excluded.covered_message_end_id,
                  covered_message_count = excluded.covered_message_count,
                  updated_at = excluded.updated_at,
                  summary_model = excluded.summary_model,
                  summary_policy_version = excluded.summary_policy_version,
                  status = excluded.status
                """,
                (
                    summary.session_id,
                    summary.task_id,
                    summary.summary_text,
                    summary.summary_version,
                    summary.covered_message_start_id,
                    summary.covered_message_end_id,
                    summary.covered_message_count,
                    summary.updated_at,
                    summary.summary_model,
                    summary.summary_policy_version,
                    summary.status,
                ),
            )

    def get_summary(self, *, session_id: str, task_id: str) -> TaskSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_summaries WHERE session_id = ? AND task_id = ?",
                (session_id, task_id),
            ).fetchone()
        if row is None:
            return None
        return TaskSummary(
            session_id=str(row["session_id"]),
            task_id=str(row["task_id"]),
            summary_text=str(row["summary_text"]),
            summary_version=int(row["summary_version"]),
            covered_message_start_id=row["covered_message_start_id"],
            covered_message_end_id=row["covered_message_end_id"],
            covered_message_count=int(row["covered_message_count"]),
            updated_at=str(row["updated_at"]),
            summary_model=str(row["summary_model"]),
            summary_policy_version=str(row["summary_policy_version"]),
            status=row["status"],
        )

    def task_index(self, *, session_id: str, limit: int = 5) -> list[TaskSegment]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_segments
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            return [
                TaskSegment(
                    task_id=str(row["task_id"]),
                    session_id=str(row["session_id"]),
                    client_id=str(row["client_id"]),
                    user_id=row["user_id"],
                    primary_skill_id=str(row["primary_skill_id"]),
                    invoked_skills=list(json.loads(row["invoked_skills_json"] or "[]")),
                    task_title=str(row["task_title"]),
                    status=row["status"],
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                    boundary_start_message_id=row["boundary_start_message_id"],
                    boundary_reason=row["boundary_reason"],
                )
                for row in rows
            ]


def build_task_summary_instruction(summary: TaskSummary | None) -> str | None:
    if summary is None or not summary.summary_text.strip():
        return None
    return (
        "【当前任务前情提要】\n"
        "用途：仅用于保持本 session 内当前 task 连贯；不代表长期客户事实，"
        "不得自动写入 Mem0/Hindsight/Asset/Graphiti。\n"
        f"- task_id：{summary.task_id}\n"
        f"- 覆盖消息数：{summary.covered_message_count}\n"
        f"- 更新时间：{summary.updated_at}\n"
        f"{summary.summary_text.strip()}"
    )


def build_task_index_instruction(tasks: list[TaskSegment]) -> str | None:
    if not tasks:
        return None
    lines = [
        "【本 session 任务目录（短索引）】",
        "仅用于知道本 session 内曾讨论过哪些任务；不要把其它任务细节混入当前任务。",
    ]
    for t in tasks:
        lines.append(f"- {t.task_title}（task_id={t.task_id}, status={t.status}）")
    return "\n".join(lines)
