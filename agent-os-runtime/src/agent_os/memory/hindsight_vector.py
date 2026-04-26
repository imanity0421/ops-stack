from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent_os.knowledge.asset_store import OpenAIEmbeddingConfig, _embed_text_openai

logger = logging.getLogger(__name__)
_VECTOR_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class HindsightVectorHit:
    event_id: str
    distance: float


def _row_text(row: dict[str, Any]) -> str:
    tags = row.get("tags")
    tag_text = ", ".join(str(x) for x in tags) if isinstance(tags, list) else ""
    return "\n".join(
        [
            f"类型：{row.get('type') or ''}",
            f"经验：{row.get('text') or ''}",
            f"结果：{row.get('outcome') or ''}",
            f"标签：{tag_text}",
            f"skill：{row.get('skill_id') or ''}",
            f"deliverable：{row.get('deliverable_type') or ''}",
        ]
    ).strip()


def _safe_float(value: Any, default: float = 999999.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _matches_metadata(
    row: dict[str, Any],
    *,
    client_id: str,
    user_id: str | None,
    task_id: str | None,
    skill_id: str | None,
    deliverable_type: str | None,
) -> bool:
    if row.get("client_id") != client_id:
        return False
    if user_id is not None and row.get("user_id") not in (None, "", user_id):
        return False
    if task_id is not None and row.get("task_id") not in (None, "", task_id):
        return False
    if skill_id is not None and row.get("skill_id") not in (None, "", skill_id):
        return False
    if deliverable_type is not None and row.get("deliverable_type") not in (
        None,
        "",
        deliverable_type,
    ):
        return False
    return True


def _lance_str_literal(value: str) -> str:
    return str(value).replace("'", "''")


class HindsightVectorIndex:
    """Hindsight 的 LanceDB 派生向量索引。

    原始事实仍以 JSONL append-only 为准；本索引只用于 Hybrid Recall 候选路由，
    可以随时 invalidate/rebuild。
    """

    def __init__(
        self,
        *,
        path: Path,
        table_name: str = "hindsight_events",
        embedding: OpenAIEmbeddingConfig | None = None,
    ) -> None:
        self._path = path
        self._table_name = table_name
        self._embedding = embedding or OpenAIEmbeddingConfig()
        self._db = None
        self._table = None

    def _connect(self) -> None:
        if self._db is not None:
            return
        try:
            import lancedb  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("未安装 lancedb；请安装 agent-os-runtime 的 asset-store 依赖") from e
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._path))

    def _open_table(self) -> bool:
        if self._table is not None:
            return True
        self._connect()
        try:
            self._table = self._db.open_table(self._table_name)  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    def _to_index_row(
        self,
        row: dict[str, Any],
        *,
        source_path: str,
        source_signature: dict[str, int],
    ) -> dict[str, Any] | None:
        event_id = str(row.get("event_id") or "").strip()
        text = str(row.get("text") or "").strip()
        if not event_id or not text:
            return None
        return {
            "schema_version": _VECTOR_SCHEMA_VERSION,
            "event_id": event_id,
            "source_path": source_path,
            "source_size": int(source_signature.get("size", 0)),
            "source_mtime_ns": int(source_signature.get("mtime_ns", 0)),
            "embedding_model": self._embedding.model,
            "client_id": row.get("client_id"),
            "user_id": row.get("user_id"),
            "task_id": row.get("task_id"),
            "skill_id": row.get("skill_id"),
            "deliverable_type": row.get("deliverable_type"),
            "type": row.get("type"),
            "outcome": row.get("outcome"),
            "text": text,
            "retrieval_text": _row_text(row),
            "recorded_at": row.get("recorded_at"),
            "event_at": row.get("event_at"),
        }

    def _embed_rows(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        source_path: str,
        source_signature: dict[str, int],
    ) -> list[dict[str, Any]]:
        indexed: list[dict[str, Any]] = []
        for row in rows:
            item = self._to_index_row(
                row,
                source_path=source_path,
                source_signature=source_signature,
            )
            if item is None:
                continue
            item["vector"] = _embed_text_openai(item["retrieval_text"], cfg=self._embedding)
            indexed.append(item)
        return indexed

    def rebuild(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        source_path: str,
        source_signature: dict[str, int],
    ) -> dict[str, Any]:
        self._connect()
        indexed = self._embed_rows(
            rows,
            source_path=source_path,
            source_signature=source_signature,
        )
        if not indexed:
            self.invalidate()
            return {"status": "ok", "row_count": 0, "path": str(self._path), "table": self._table_name}
        if self._open_table():
            try:
                source = _lance_str_literal(source_path)
                self._table.delete(f"source_path == '{source}'")  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("Hindsight vector index delete-before-rebuild failed: %s", e)
                return {
                    "status": "error",
                    "reason": "delete_before_rebuild_failed",
                    "error": str(e),
                    "path": str(self._path),
                    "table": self._table_name,
                }
        if not self._open_table():
            self._table = self._db.create_table(self._table_name, data=indexed)  # type: ignore[union-attr]
        else:
            self._table.add(indexed)  # type: ignore[union-attr]
        return {
            "status": "ok",
            "row_count": len(indexed),
            "path": str(self._path),
            "table": self._table_name,
        }

    def append(
        self,
        row: dict[str, Any],
        *,
        source_path: str,
        source_signature: dict[str, int],
    ) -> None:
        self._connect()
        indexed = self._embed_rows(
            [row],
            source_path=source_path,
            source_signature=source_signature,
        )
        if not indexed:
            return
        if not self._open_table():
            self._table = self._db.create_table(self._table_name, data=indexed)  # type: ignore[union-attr]
        else:
            source = _lance_str_literal(source_path)
            event_id = _lance_str_literal(str(indexed[0].get("event_id") or ""))
            if event_id:
                self._table.delete(  # type: ignore[union-attr]
                    f"source_path == '{source}' AND event_id == '{event_id}'"
                )
            self._table.add(indexed)  # type: ignore[union-attr]

    def invalidate(self) -> bool:
        self._connect()
        try:
            self._db.drop_table(self._table_name)  # type: ignore[union-attr]
            self._table = None
            return True
        except Exception:
            return False

    def status(
        self,
        *,
        source_path: str | None = None,
        source_signature: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if not self._open_table():
            return {"exists": False, "path": str(self._path), "table": self._table_name}
        try:
            data = self._table.to_arrow().to_pydict()  # type: ignore[union-attr]
        except Exception as e:
            return {"exists": True, "status": "error", "error": str(e)}
        event_ids = data.get("event_id") or []
        source_paths = data.get("source_path") or []
        source_sizes = data.get("source_size") or []
        source_mtimes = data.get("source_mtime_ns") or []
        schemas = data.get("schema_version") or []
        models = data.get("embedding_model") or []
        fresh = None
        if source_path is not None and source_signature is not None:
            source_row_indexes = [i for i, p in enumerate(source_paths) if p == source_path]
            fresh = bool(source_row_indexes)
            has_current_signature = False
            for i in source_row_indexes:
                if (
                    int(schemas[i] or 0) != _VECTOR_SCHEMA_VERSION
                    or str(models[i] or "") != self._embedding.model
                ):
                    fresh = False
                    break
                if (
                    int(source_sizes[i] or 0) == int(source_signature.get("size", 0))
                    and int(source_mtimes[i] or 0) == int(source_signature.get("mtime_ns", 0))
                ):
                    has_current_signature = True
            fresh = fresh and has_current_signature
        return {
            "exists": True,
            "path": str(self._path),
            "table": self._table_name,
            "row_count": len(event_ids),
            "schema_version": _VECTOR_SCHEMA_VERSION,
            "embedding_model": self._embedding.model,
            "fresh": fresh,
        }

    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        task_id: str | None,
        skill_id: str | None,
        deliverable_type: str | None,
        limit: int,
    ) -> list[HindsightVectorHit]:
        if not self._open_table():
            return []
        qv = _embed_text_openai(query, cfg=self._embedding)
        over = max(limit * 30, 80)
        rows = self._table.search(qv, vector_column_name="vector").limit(over).to_list()  # type: ignore[union-attr]
        event_ids: list[tuple[float, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not _matches_metadata(
                row,
                client_id=client_id,
                user_id=user_id,
                task_id=task_id,
                skill_id=skill_id,
                deliverable_type=deliverable_type,
            ):
                continue
            event_id = str(row.get("event_id") or "").strip()
            if event_id:
                event_ids.append((_safe_float(row.get("_distance")), event_id))
        event_ids.sort(key=lambda x: x[0])
        seen: set[str] = set()
        out: list[HindsightVectorHit] = []
        for distance, event_id in event_ids:
            if event_id in seen:
                continue
            seen.add(event_id)
            out.append(HindsightVectorHit(event_id=event_id, distance=distance))
            if len(out) >= limit:
                break
        return out
