"""Agno 会话存储：按 Settings 创建 ``BaseDb``，供 ``Agent(db=...)`` 使用。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agno.db.base import BaseDb

    from ops_agent.config import Settings


def create_session_db(settings: "Settings") -> "BaseDb | None":
    """
    若 ``enable_session_db`` 为假则返回 None（不落库、不注入历史）。

    优先级：
    1. ``OPS_SESSION_DB_URL`` 非空：按 scheme 选择 ``SqliteDb`` / ``PostgresDb`` / ``RedisDb``；
       无 ``://`` 的字符串视为**本地文件路径**（仍走 Sqlite 文件）。
    2. 否则使用 ``OPS_SESSION_DB_PATH`` 指向的 Sqlite 文件（默认 ``data/agno_session.db``）。
    """
    if not settings.enable_session_db:
        return None

    from agno.db.sqlite import SqliteDb

    raw = (settings.session_db_url or "").strip()
    if not raw:
        p = settings.session_sqlite_path.resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return SqliteDb(db_file=str(p))

    lower = raw.lower()
    if "://" in lower:
        if lower.startswith("redis://") or lower.startswith("rediss://"):
            from agno.db.redis import RedisDb

            return RedisDb(db_url=raw)
        if lower.startswith("postgres://") or lower.startswith("postgresql://"):
            from agno.db.postgres import PostgresDb

            return PostgresDb(db_url=raw)
        if lower.startswith("sqlite:"):
            return SqliteDb(db_url=raw)
        msg = f"不支持的 OPS_SESSION_DB_URL scheme: {raw!r}（支持 sqlite:、postgres(ql)://、redis://）"
        raise ValueError(msg)

    p = Path(raw).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return SqliteDb(db_file=str(p))


def session_db_summary(db: Any) -> str:
    """用于日志/inspect 的一行说明（不含密钥）。"""
    cls = type(db).__name__
    for attr in ("db_file", "db_url", "id"):
        v = getattr(db, attr, None)
        if v is not None and str(v).strip():
            return f"{cls}({attr}={v!r})"
    return cls
