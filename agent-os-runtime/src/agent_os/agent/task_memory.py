from __future__ import annotations

import json
import os
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

TaskStatus = Literal["active", "inactive", "closed", "archived"]
TaskEntityStatus = Literal["active", "archived"]
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
    text = " ".join(_text_or_empty(message).strip().split())
    if not text:
        return "未命名任务"
    return text[:max_chars]


def _text_or_empty(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _decode_invoked_skills(raw: str | None, *, fallback: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        data = []
    skills = [str(x) for x in data if isinstance(x, str) and x.strip()]
    return skills or ([fallback] if fallback else [])


@dataclass(frozen=True)
class TaskEntity:
    task_id: str
    name: str
    status: TaskEntityStatus
    created_at: str
    current_main_session_id: str


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


@dataclass(frozen=True)
class TaskMessage:
    message_id: str
    session_id: str
    task_id: str
    role: MessageRole
    content: str
    created_at: str
    sequence_no: int


def _task_entity_from_row(row: sqlite3.Row | None) -> TaskEntity | None:
    if row is None:
        return None
    return TaskEntity(
        task_id=str(row["task_id"]),
        name=str(row["name"]),
        status=row["status"],
        created_at=str(row["created_at"]),
        current_main_session_id=str(row["current_main_session_id"]),
    )


class TaskMemoryStore:
    """同一 session 内的 task working memory 存储；不承载跨 session 长期记忆。"""

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
                CREATE TABLE IF NOT EXISTS sessions (
                  session_id TEXT PRIMARY KEY,
                  client_id TEXT NOT NULL,
                  user_id TEXT,
                  active_task_id TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  current_main_session_id TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                  ON tasks(status, created_at);

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

    def create_task(
        self,
        *,
        name: str,
        current_main_session_id: str,
        task_id: str | None = None,
    ) -> TaskEntity:
        tid = task_id or new_task_id()
        title = fallback_task_title(name, max_chars=80)
        now = _iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks
                  (task_id, name, status, created_at, current_main_session_id)
                VALUES (?, ?, 'active', ?, ?)
                """,
                (tid, title, now, current_main_session_id),
            )
        return TaskEntity(
            task_id=tid,
            name=title,
            status="active",
            created_at=now,
            current_main_session_id=current_main_session_id,
        )

    def get_task_entity(self, task_id: str) -> TaskEntity | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_entity_from_row(row)

    def list_task_entities(
        self,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[TaskEntity]:
        where = "" if include_archived else "WHERE status = 'active'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tasks
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [_task_entity_from_row(row) for row in rows if row is not None]

    def set_task_entity_status(self, task_id: str, status: TaskEntityStatus) -> TaskEntity | None:
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_entity_from_row(row)

    def archive_task_entity(self, task_id: str) -> TaskEntity | None:
        return self.set_task_entity_status(task_id, "archived")

    def unarchive_task_entity(self, task_id: str) -> TaskEntity | None:
        return self.set_task_entity_status(task_id, "active")

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
            conn.execute(
                """
                INSERT OR IGNORE INTO tasks
                  (task_id, name, status, created_at, current_main_session_id)
                VALUES (?, ?, 'active', ?, ?)
                """,
                (task_id, title, now, session_id),
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
            invoked_skills=_decode_invoked_skills(
                row["invoked_skills_json"], fallback=str(row["primary_skill_id"])
            ),
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

    def task_messages(self, *, session_id: str, task_id: str) -> list[TaskMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND task_id = ?
                ORDER BY sequence_no ASC
                """,
                (session_id, task_id),
            ).fetchall()
        return [
            TaskMessage(
                message_id=str(row["message_id"]),
                session_id=str(row["session_id"]),
                task_id=str(row["task_id"]),
                role=row["role"],
                content=str(row["content"]),
                created_at=str(row["created_at"]),
                sequence_no=int(row["sequence_no"]),
            )
            for row in rows
        ]

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
                    invoked_skills=_decode_invoked_skills(
                        row["invoked_skills_json"], fallback=str(row["primary_skill_id"])
                    ),
                    task_title=str(row["task_title"]),
                    status=row["status"],
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                    boundary_start_message_id=row["boundary_start_message_id"],
                    boundary_reason=row["boundary_reason"],
                )
                for row in rows
            ]


def _shorten(text: str, max_chars: int) -> str:
    t = " ".join(_text_or_empty(text).strip().split())
    if max_chars <= 0:
        return ""
    if len(t) <= max_chars:
        return t
    if max_chars <= 3:
        return t[:max_chars]
    return t[: max_chars - 3] + "..."


def _fallback_summary(
    *,
    existing_summary: str,
    messages: list[TaskMessage],
    max_chars: int,
) -> str:
    recent = messages[-8:]
    user_lines = [
        f"- {_shorten(m.content, 120)}"
        for m in recent
        if m.role == "user" and _text_or_empty(m.content).strip()
    ]
    assistant_lines = [
        f"- {_shorten(m.content, 120)}"
        for m in recent
        if m.role == "assistant" and _text_or_empty(m.content).strip()
    ]
    parts = [
        "- 当前任务目标：根据本 task 内最新消息继续推进。",
        "- 用户已确认的约束：",
        *(user_lines[:3] or ["- （暂无明确约束）"]),
        "- 已做出的关键决定：",
        *(assistant_lines[:2] or ["- （暂无明确决定）"]),
        "- 当前交付物状态：继续沿用最近上下文。",
        "- 待办/未决问题：优先响应用户最新请求。",
        "- 不要重复尝试的方向：避免重复已被用户否定的表述。",
        "- 最近一次用户反馈："
        + (f" {_shorten(user_lines[-1].lstrip('- '), 160)}" if user_lines else " （暂无）"),
    ]
    if _text_or_empty(existing_summary).strip():
        parts.insert(0, "- 既有前情：" + _shorten(existing_summary, 240))
    return _shorten("\n".join(parts), max_chars)


def _llm_summary(
    *,
    existing_summary: str,
    messages: list[TaskMessage],
    model: str | None,
    max_chars: int,
) -> str | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_API_BASE") or None,
        )
        mid = (
            model
            or os.getenv("AGENT_OS_TASK_SUMMARY_MODEL")
            or os.getenv("AGENT_OS_MODEL", "gpt-4o-mini")
        )
        transcript = "\n".join(f"{m.role}: {_shorten(m.content, 1200)}" for m in messages[-24:])
        prompt = (
            "你是同一 session 内当前 task 的工作记忆压缩器。"
            "请基于既有 summary 与新增对话，更新一份用于下一轮继续工作的结构化摘要。"
            "不要加入长期事实，不要写入 Mem0/Hindsight/Asset/Graphiti。"
            f"总长度不超过 {max_chars} 字。必须使用这些小标题：\n"
            "- 当前任务目标：\n"
            "- 用户已确认的约束：\n"
            "- 已做出的关键决定：\n"
            "- 当前交付物状态：\n"
            "- 待办/未决问题：\n"
            "- 不要重复尝试的方向：\n"
            "- 最近一次用户反馈：\n\n"
            f"既有 summary：\n{existing_summary or '（无）'}\n\n"
            f"对话：\n{transcript}"
        )
        r = client.chat.completions.create(
            model=mid,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        text = (r.choices[0].message.content or "").strip()
        return _shorten(text, max_chars) if text else None
    except Exception:
        return None


class TaskSummaryService:
    """为 TaskMemoryStore 生成滚动 summary；只服务当前 session/task 连贯性。"""

    def __init__(
        self,
        store: TaskMemoryStore,
        *,
        model: str | None = None,
        max_chars: int = 800,
        min_messages: int = 8,
        every_n_messages: int = 6,
    ) -> None:
        self._store = store
        self._model = model
        self._max_chars = max(1, int(max_chars))
        self._min_messages = max(1, int(min_messages))
        self._every_n_messages = max(1, int(every_n_messages))

    def maybe_update(self, *, session_id: str, task_id: str) -> TaskSummary | None:
        messages = self._store.task_messages(session_id=session_id, task_id=task_id)
        if len(messages) < self._min_messages:
            return None
        current = self._store.get_summary(session_id=session_id, task_id=task_id)
        covered = current.covered_message_count if current is not None else 0
        if current is not None and len(messages) - covered < self._every_n_messages:
            return None
        existing_text = current.summary_text if current is not None else ""
        text = _llm_summary(
            existing_summary=existing_text,
            messages=messages,
            model=self._model,
            max_chars=self._max_chars,
        ) or _fallback_summary(
            existing_summary=existing_text,
            messages=messages,
            max_chars=self._max_chars,
        )
        summary = TaskSummary(
            session_id=session_id,
            task_id=task_id,
            summary_text=text,
            summary_version=(current.summary_version + 1) if current is not None else 1,
            covered_message_start_id=messages[0].message_id if messages else None,
            covered_message_end_id=messages[-1].message_id if messages else None,
            covered_message_count=len(messages),
            updated_at=_iso(),
            summary_model=self._model or os.getenv("AGENT_OS_TASK_SUMMARY_MODEL") or "fallback",
            summary_policy_version="task_summary_v1",
            status="working",
        )
        self._store.upsert_summary(summary)
        return summary


def build_task_summary_instruction(summary: TaskSummary | None) -> str | None:
    summary_text = _text_or_empty(getattr(summary, "summary_text", None)).strip()
    if summary is None or not summary_text:
        return None
    return (
        "【当前任务前情提要】\n"
        "用途：仅用于当前 session/task 连贯性；不代表长期事实，"
        "不得自动写入 Mem0，不得自动写入 Hindsight，"
        "不得自动写入 Asset，不得自动写入 Graphiti。\n"
        f"- task_id：{summary.task_id}\n"
        f"- 覆盖消息数：{summary.covered_message_count}\n"
        f"- 更新时间：{summary.updated_at}\n"
        f"{summary_text}"
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
