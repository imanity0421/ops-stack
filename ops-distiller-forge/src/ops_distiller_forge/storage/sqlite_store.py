from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ops_distiller_forge.ontology.models import KnowledgePoint


class SqliteKnowledgeStore:
    """知识点索引：全量 JSON 在 JSONL 真源，此处便于按课/版本查询。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _init_schema(self) -> None:
        with self._connect() as cx:
            cx.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_point (
                    id TEXT PRIMARY KEY,
                    handbook_version TEXT NOT NULL,
                    lesson_key TEXT,
                    title TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_kp_hv ON knowledge_point(handbook_version)"
            )
            cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_kp_lesson ON knowledge_point(lesson_key)"
            )

    def upsert(self, kp: KnowledgePoint) -> None:
        payload = kp.model_dump_json(ensure_ascii=False)
        lesson_key = kp.metadata.source_relpath
        with self._connect() as cx:
            cx.execute(
                """
                INSERT OR REPLACE INTO knowledge_point
                (id, handbook_version, lesson_key, title, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kp.id,
                    kp.metadata.handbook_version,
                    lesson_key,
                    kp.title,
                    payload,
                    kp.metadata.ingested_at_utc,
                ),
            )
