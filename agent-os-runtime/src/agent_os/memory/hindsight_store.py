from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_os.memory.hindsight_index import route_hindsight_candidates, semantic_cluster_key
from agent_os.memory.hindsight_retrieval import query_features
from agent_os.memory.hindsight_retrieval import (
    DEFAULT_HINDSIGHT_RETRIEVAL_POLICY,
    HindsightScore,
    recorded_epoch,
)
from agent_os.memory.hindsight_vector import HindsightVectorIndex
from agent_os.memory.models import UserFact

logger = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_INDEX_SCHEMA_VERSION = 1


@contextmanager
def _file_lock(path: Path, *, timeout_sec: float = 10.0):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    with lock_path.open("a+b") as lock_file:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - Windows CI exercises the primary path here.
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"无法获得 Hindsight 文件锁: {lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class HindsightReinforcementSignals:
    recurrence_count: int | None = None
    negative_evidence_count: int | None = None
    last_reinforced_at: datetime | None = None


def _normalize_merge_text(text: str) -> str:
    """用于同类合并：折叠空白 + strip + casefold。"""
    return _WS.sub(" ", (text or "").strip()).casefold()


def _merge_bucket_key(row: dict[str, Any]) -> str:
    typ = str(row.get("type") or "")
    return f"{typ}\n{_normalize_merge_text(str(row.get('text') or ''))}"


def _budget_cluster_key(row: dict[str, Any]) -> str:
    return semantic_cluster_key(row)


def _row_event_id(row: dict[str, Any]) -> str:
    eid = row.get("event_id")
    return str(eid).strip() if eid is not None else ""


def _row_weight(row: dict[str, Any]) -> int:
    try:
        w = int(row.get("weight_count", 1) or 1)
    except (TypeError, ValueError):
        w = 1
    return max(1, min(w, 10000))


def _recorded_epoch(row: dict[str, Any]) -> float:
    return recorded_epoch(row)


def _bounded_int(value: Any, *, lower: int, upper: int) -> int | None:
    if value is None:
        return None
    try:
        return max(lower, min(int(value), upper))
    except (TypeError, ValueError):
        return None


def _freq_merge_enabled() -> bool:
    return os.getenv("AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE", "1").lower() not in (
        "0",
        "false",
        "no",
    )


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Hindsight 文件无法读取，返回空结果: %s (%s)", path, e)
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _score_hindsight_row(
    row: dict[str, Any],
    *,
    qtokens: set[str],
    user_id: str | None,
    task_id: str | None,
    skill_id: str | None,
    deliverable_type: str | None,
    superseded: bool = False,
) -> float:
    return _explain_hindsight_row(
        row,
        qtokens=qtokens,
        user_id=user_id,
        task_id=task_id,
        skill_id=skill_id,
        deliverable_type=deliverable_type,
        superseded=superseded,
    ).score


def _explain_hindsight_row(
    row: dict[str, Any],
    *,
    qtokens: set[str],
    user_id: str | None,
    task_id: str | None,
    skill_id: str | None,
    deliverable_type: str | None,
    superseded: bool = False,
) -> HindsightScore:
    return DEFAULT_HINDSIGHT_RETRIEVAL_POLICY.score_row(
        row,
        qtokens=qtokens,
        user_id=user_id,
        task_id=task_id,
        skill_id=skill_id,
        deliverable_type=deliverable_type,
        superseded=superseded,
    )


def _render_debug(rendered: str, score: HindsightScore) -> str:
    reasons = ", ".join(score.reasons) if score.reasons else "none"
    return f"{rendered} [score={score.score:.2f} | reasons={reasons}]"


def _superseded_budget(limit: int) -> int:
    return max(1, max(limit, 1) // 4)


def _cluster_budget(limit: int) -> int:
    return max(1, math.ceil(max(limit, 1) / 2))


def _metadata_budget(limit: int) -> int:
    return max(1, math.ceil(max(limit, 1) * 0.75))


def _route_candidate_limit(limit: int) -> int:
    return max(80, max(limit, 1) * 20)


def _metadata_key(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class _BudgetState:
    limit: int
    requested_user_id: str | None
    requested_task_id: str | None
    requested_skill_id: str | None
    requested_deliverable_type: str | None
    seen: set[str] = field(default_factory=set)
    superseded_count: int = 0
    cluster_counts: dict[str, int] = field(default_factory=dict)
    user_counts: dict[str, int] = field(default_factory=dict)
    task_counts: dict[str, int] = field(default_factory=dict)
    skill_counts: dict[str, int] = field(default_factory=dict)
    deliverable_counts: dict[str, int] = field(default_factory=dict)

    def allow(
        self,
        *,
        rendered: str,
        superseded: bool,
        cluster_key: str,
        user_key: str,
        task_key: str,
        skill_key: str,
        deliverable_key: str,
    ) -> bool:
        if rendered in self.seen:
            return False
        if superseded and self.superseded_count >= _superseded_budget(self.limit):
            return False
        if self.cluster_counts.get(cluster_key, 0) >= _cluster_budget(self.limit):
            return False
        if (
            user_key
            and user_key != (self.requested_user_id or "")
            and self.user_counts.get(user_key, 0) >= _metadata_budget(self.limit)
        ):
            return False
        if (
            task_key
            and task_key != (self.requested_task_id or "")
            and self.task_counts.get(task_key, 0) >= _metadata_budget(self.limit)
        ):
            return False
        if (
            skill_key
            and skill_key != (self.requested_skill_id or "")
            and self.skill_counts.get(skill_key, 0) >= _metadata_budget(self.limit)
        ):
            return False
        if (
            deliverable_key
            and deliverable_key != (self.requested_deliverable_type or "")
            and self.deliverable_counts.get(deliverable_key, 0) >= _metadata_budget(self.limit)
        ):
            return False
        return True

    def add(
        self,
        *,
        rendered: str,
        superseded: bool,
        cluster_key: str,
        user_key: str,
        task_key: str,
        skill_key: str,
        deliverable_key: str,
    ) -> None:
        self.seen.add(rendered)
        if superseded:
            self.superseded_count += 1
        self.cluster_counts[cluster_key] = self.cluster_counts.get(cluster_key, 0) + 1
        if user_key and user_key != (self.requested_user_id or ""):
            self.user_counts[user_key] = self.user_counts.get(user_key, 0) + 1
        if task_key and task_key != (self.requested_task_id or ""):
            self.task_counts[task_key] = self.task_counts.get(task_key, 0) + 1
        if skill_key and skill_key != (self.requested_skill_id or ""):
            self.skill_counts[skill_key] = self.skill_counts.get(skill_key, 0) + 1
        if deliverable_key and deliverable_key != (self.requested_deliverable_type or ""):
            self.deliverable_counts[deliverable_key] = (
                self.deliverable_counts.get(deliverable_key, 0) + 1
            )


def _append_with_budget(
    out: list[str],
    *,
    rendered: str,
    superseded: bool,
    cluster_key: str,
    user_key: str,
    task_key: str,
    skill_key: str,
    deliverable_key: str,
    budget: _BudgetState,
) -> None:
    if not budget.allow(
        rendered=rendered,
        superseded=superseded,
        cluster_key=cluster_key,
        user_key=user_key,
        task_key=task_key,
        skill_key=skill_key,
        deliverable_key=deliverable_key,
    ):
        return
    budget.add(
        rendered=rendered,
        superseded=superseded,
        cluster_key=cluster_key,
        user_key=user_key,
        task_key=task_key,
        skill_key=skill_key,
        deliverable_key=deliverable_key,
    )
    out.append(rendered)


def _append_relaxed(
    out: list[str],
    *,
    rendered: str,
    budget: _BudgetState,
) -> None:
    if rendered in budget.seen:
        return
    budget.seen.add(rendered)
    out.append(rendered)


def _render_row(row: dict[str, Any], *, temporal_grounding: bool) -> str:
    text = str(row.get("text") or "")
    recorded = row.get("recorded_at") or row.get("created_at") or "记录时间未知"
    event_at = row.get("event_at") or "发生时间未知"
    source = row.get("source") or "unknown"
    if temporal_grounding:
        return f"[发生于 {event_at} | 记录于 {recorded} | 来源 {source}] {text}"
    return text


class HindsightStore:
    """
    任务反馈与复盘教训的本地存储（JSONL）。
    每行 JSON 至少含：type, client_id, text；type 为 feedback | lesson。

    检索侧支持：

    - **supersedes_event_id**：若行 B 含 ``supersedes_event_id`` 指向行 A 的 ``event_id``，
      A 会在召回评分中降权，但原始行仍保留且可被审计/调试检索看到。
    - **同类合并 / 频次**：规范化正文 + ``type`` 相同的行合并为一条展示，并在分数上给予对数加成；``weight_count`` 参与「总权重」展示。
    """

    def __init__(
        self,
        path: Path,
        *,
        enable_vector_recall: bool = False,
        vector_index_path: Path | None = None,
        vector_candidate_limit: int = 160,
        vector_score_weight: float = 6.0,
    ) -> None:
        self._path = path
        self._index_path = path.with_name(f"{path.name}.index.json")
        self._enable_vector_recall = enable_vector_recall
        self._vector_candidate_limit = max(1, int(vector_candidate_limit))
        self._vector_score_weight = max(0.0, float(vector_score_weight))
        self._vector_index = (
            HindsightVectorIndex(
                path=vector_index_path or path.with_name(f"{path.stem}.hindsight_vector.lancedb")
            )
            if enable_vector_recall
            else None
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _apply_vector_rerank(self, score: HindsightScore, row: dict[str, Any]) -> HindsightScore:
        if self._vector_score_weight <= 0 or row.get("_vector_distance") is None:
            return score
        try:
            distance = max(0.0, float(row.get("_vector_distance")))
        except (TypeError, ValueError):
            return score
        similarity = 1.0 / (1.0 + distance)
        bonus = similarity * self._vector_score_weight
        return HindsightScore(
            score=score.score + bonus,
            reasons=[
                *score.reasons,
                f"vector_distance={distance:.4f}",
                f"vector_bonus={bonus:.2f}",
            ],
        )

    def _file_signature(self) -> dict[str, int]:
        stat = self._path.stat()
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}

    def _write_index(self, rows: list[dict[str, Any]]) -> None:
        if not self._path.is_file():
            return
        payload = {
            "schema_version": _INDEX_SCHEMA_VERSION,
            "source": self._path.name,
            **self._file_signature(),
            "rows": rows,
        }
        try:
            self._index_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Hindsight 派生索引写入失败: %s (%s)", self._index_path, e)

    def _read_index(self) -> list[dict[str, Any]] | None:
        if not self._path.is_file() or not self._index_path.is_file():
            return None
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("Hindsight 派生索引无法读取，将回退 JSONL: %s (%s)", self._index_path, e)
            return None
        if not isinstance(payload, dict):
            return None
        sig = self._file_signature()
        if (
            payload.get("schema_version") != _INDEX_SCHEMA_VERSION
            or payload.get("source") != self._path.name
            or payload.get("size") != sig["size"]
            or payload.get("mtime_ns") != sig["mtime_ns"]
        ):
            return None
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]

    def _rows(self) -> list[dict[str, Any]]:
        indexed = self._read_index()
        if indexed is not None:
            return indexed
        rows = _read_jsonl_rows(self._path)
        self._write_index(rows)
        return rows

    def index_status(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {
                "source_exists": False,
                "index_exists": self._index_path.is_file(),
                "fresh": False,
            }
        sig = self._file_signature()
        if not self._index_path.is_file():
            return {"source_exists": True, "index_exists": False, "fresh": False, **sig}
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"source_exists": True, "index_exists": True, "fresh": False, **sig}
        fresh = (
            isinstance(payload, dict)
            and payload.get("schema_version") == _INDEX_SCHEMA_VERSION
            and payload.get("source") == self._path.name
            and payload.get("size") == sig["size"]
            and payload.get("mtime_ns") == sig["mtime_ns"]
        )
        rows = payload.get("rows") if isinstance(payload, dict) else None
        return {
            "source_exists": True,
            "index_exists": True,
            "fresh": bool(fresh),
            "row_count": len(rows) if isinstance(rows, list) else None,
            **sig,
        }

    def rebuild_index(self) -> dict[str, Any]:
        if not self._path.is_file():
            self.invalidate_index()
            return {"status": "missing_source", "row_count": 0}
        rows = _read_jsonl_rows(self._path)
        self._write_index(rows)
        return {"status": "ok", "row_count": len(rows), "index_path": str(self._index_path)}

    def vector_index_status(self) -> dict[str, Any]:
        if self._vector_index is None:
            return {"enabled": False}
        source_signature = self._file_signature() if self._path.is_file() else None
        return {
            "enabled": True,
            **self._vector_index.status(
                source_path=str(self._path),
                source_signature=source_signature,
            ),
        }

    def rebuild_vector_index(self) -> dict[str, Any]:
        if self._vector_index is None:
            return {"status": "skipped", "reason": "vector_recall_disabled"}
        rows = _read_jsonl_rows(self._path) if self._path.is_file() else []
        signature = self._file_signature() if self._path.is_file() else {"size": 0, "mtime_ns": 0}
        return self._vector_index.rebuild(
            rows,
            source_path=str(self._path),
            source_signature=signature,
        )

    def invalidate_vector_index(self) -> bool:
        if self._vector_index is None:
            return False
        return self._vector_index.invalidate()

    def delete_line(
        self,
        *,
        file_line: int,
        expected_client_id: str | None = None,
    ) -> dict[str, Any]:
        """按 JSONL 行号删除 Hindsight 行，并同步清理派生索引。

        该方法用于运维/演示接口。Hindsight 的正常治理原则仍是 append-only；
        手工删除属于垃圾数据回退或本地调试路径，因此必须和 append 共用文件锁。
        """

        with _file_lock(self._path):
            if not self._path.exists():
                return {"status": "missing_source"}
            try:
                lines = self._path.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeDecodeError) as e:
                return {"status": "error", "reason": "read_failed", "error": str(e)}
            if file_line < 1 or file_line > len(lines):
                return {"status": "error", "reason": "file_line_out_of_range"}
            raw = lines[file_line - 1].strip()
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as e:
                return {"status": "error", "reason": "invalid_json", "error": str(e)}
            if not isinstance(row, dict):
                return {"status": "error", "reason": "not_json_object"}
            if expected_client_id is not None and row.get("client_id") != expected_client_id:
                return {"status": "forbidden", "reason": "client_id_mismatch"}
            del lines[file_line - 1]
            self._path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            json_removed = self.invalidate_index()
            vector_removed = self.invalidate_vector_index()
            return {
                "status": "ok",
                "json_index_removed": json_removed,
                "vector_index_removed": vector_removed,
            }

    def invalidate_index(self) -> bool:
        """删除派生 sidecar 索引。

        当外部治理路径直接修改 JSONL（如 Web 手工删除行）时，必须同步清理索引，
        避免 sidecar 继续残留已删除的原始行副本。
        """
        try:
            self._index_path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            logger.warning("Hindsight 派生索引删除失败: %s (%s)", self._index_path, e)
            return False

    def _update_index_after_append(
        self,
        obj: dict[str, Any],
        *,
        previous_signature: dict[str, int] | None,
    ) -> None:
        if not self._index_path.is_file():
            return
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        rows = payload.get("rows")
        if (
            payload.get("schema_version") != _INDEX_SCHEMA_VERSION
            or payload.get("source") != self._path.name
            or not isinstance(rows, list)
            or previous_signature is None
            or payload.get("size") != previous_signature["size"]
            or payload.get("mtime_ns") != previous_signature["mtime_ns"]
        ):
            return
        rows.append(obj)
        payload.update(self._file_signature())
        try:
            self._index_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Hindsight 派生索引增量更新失败: %s (%s)", self._index_path, e)

    def _update_vector_after_append(self, obj: dict[str, Any]) -> None:
        if self._vector_index is None:
            return
        try:
            self._vector_index.append(
                obj,
                source_path=str(self._path),
                source_signature=self._file_signature(),
            )
        except Exception as e:
            logger.warning("Hindsight vector index 增量更新失败，将回退 JSONL 检索: %s", e)

    def append_feedback(self, fact: UserFact) -> dict[str, Any]:
        line: dict[str, Any] = {
            "memory_version": "2.0",
            "event_id": f"hst_{uuid4().hex}",
            "type": "lesson" if fact.fact_type == "lesson" else "feedback",
            "client_id": fact.client_id,
            "user_id": fact.user_id,
            "scope": fact.scope or "task_scoped",
            "skill_id": fact.skill_id,
            "task_id": fact.task_id,
            "deliverable_type": fact.deliverable_type,
            "text": fact.text,
            "impact_on_preference": fact.impact_on_preference,
            "confidence": fact.confidence,
            "outcome": fact.outcome,
            "outcome_score": fact.outcome_score,
            "is_success": fact.is_success,
            "conversion_rate": fact.conversion_rate,
            "tags": list(fact.tags),
            "evidence_refs": list(fact.evidence_refs),
            "event_at": fact.event_at.isoformat() if fact.event_at else None,
            "recorded_at": fact.recorded_at.isoformat(),
            "source": fact.source,
        }
        if fact.validity_score is not None:
            line["validity_score"] = fact.validity_score
        if fact.specificity_score is not None:
            line["specificity_score"] = fact.specificity_score
        if fact.recurrence_count is not None:
            line["recurrence_count"] = fact.recurrence_count
        if fact.negative_evidence_count is not None:
            line["negative_evidence_count"] = fact.negative_evidence_count
        if fact.last_reinforced_at is not None:
            line["last_reinforced_at"] = fact.last_reinforced_at.isoformat()
        if fact.supersedes_event_id:
            line["supersedes_event_id"] = str(fact.supersedes_event_id).strip()
        if fact.weight_count != 1:
            line["weight_count"] = int(fact.weight_count)
        self._append_line(line)
        return {"status": "ok", "path": str(self._path)}

    def append_lesson(
        self,
        *,
        client_id: str,
        text: str,
        user_id: str | None = None,
        task_id: str | None = None,
        skill_id: str | None = None,
        source: str = "async_review",
        confidence: float | None = None,
        tags: list[str] | None = None,
        event_at: datetime | None = None,
        supersedes_event_id: str | None = None,
        weight_count: int = 1,
        validity_score: float | None = None,
        specificity_score: float | None = None,
        recurrence_count: int | None = None,
        negative_evidence_count: int | None = None,
        last_reinforced_at: datetime | None = None,
    ) -> dict[str, Any]:
        wc = max(1, min(int(weight_count or 1), 10000))
        line: dict[str, Any] = {
            "memory_version": "2.0",
            "event_id": f"hst_{uuid4().hex}",
            "type": "lesson",
            "client_id": client_id,
            "user_id": user_id,
            "task_id": task_id,
            "skill_id": skill_id,
            "text": text.strip(),
            "source": source,
            "scope": "task_scoped" if task_id else "client_shared",
            "confidence": confidence,
            "tags": list(tags or []),
            "event_at": event_at.isoformat() if event_at else None,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if validity_score is not None:
            line["validity_score"] = max(0.0, min(float(validity_score), 1.0))
        if specificity_score is not None:
            line["specificity_score"] = max(0.0, min(float(specificity_score), 1.0))
        if recurrence_count is not None:
            line["recurrence_count"] = max(1, min(int(recurrence_count), 10000))
        if negative_evidence_count is not None:
            line["negative_evidence_count"] = max(0, min(int(negative_evidence_count), 10000))
        if last_reinforced_at is not None:
            line["last_reinforced_at"] = last_reinforced_at.isoformat()
        if supersedes_event_id:
            line["supersedes_event_id"] = str(supersedes_event_id).strip()
        if wc != 1:
            line["weight_count"] = wc
        self._append_line(line)
        return {"status": "ok", "path": str(self._path)}

    def _append_line(self, obj: dict[str, Any]) -> None:
        with _file_lock(self._path):
            previous_signature = self._file_signature() if self._path.is_file() else None
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._update_index_after_append(obj, previous_signature=previous_signature)
            self._update_vector_after_append(obj)

    def reinforcement_signals(
        self,
        *,
        text: str,
        client_id: str,
        user_id: str | None = None,
        task_id: str | None = None,
        skill_id: str | None = None,
        deliverable_type: str | None = None,
        observed_at: datetime | None = None,
    ) -> HindsightReinforcementSignals:
        """从同租户相似历史 lesson 中推导本次写入的强化信号。

        该方法不写回历史行；`last_reinforced_at` 表示本次 review 观察到相似经验
        再次出现的系统观测时间。
        """
        if not self._path.is_file():
            return HindsightReinforcementSignals()
        q = query_features(text)
        if not q:
            return HindsightReinforcementSignals()
        rows: list[dict[str, Any]] = []
        for row in self._rows():
            if row.get("client_id") != client_id:
                continue
            if row.get("type") not in ("feedback", "lesson"):
                continue
            row_text = row.get("text") or ""
            if not isinstance(row_text, str) or not row_text:
                continue
            features = query_features(row_text)
            overlap = len(q & features)
            denom = max(1, min(len(q), len(features)))
            if overlap / float(denom) < 0.45:
                continue
            score = overlap
            if user_id and row.get("user_id") == user_id:
                score += 2
            if task_id and row.get("task_id") == task_id:
                score += 3
            if skill_id and row.get("skill_id") == skill_id:
                score += 2
            if deliverable_type and row.get("deliverable_type") == deliverable_type:
                score += 1
            rows.append({"_match_score": score, **row})

        if not rows:
            return HindsightReinforcementSignals()
        rows.sort(key=lambda r: (-int(r.get("_match_score", 0)), -_recorded_epoch(r)))
        top = rows[:8]
        recurrence = 1 + len(top)
        negative = 0
        for row in top:
            neg = _bounded_int(row.get("negative_evidence_count"), lower=0, upper=10000)
            if neg:
                negative += neg
        return HindsightReinforcementSignals(
            recurrence_count=max(2, min(recurrence, 10000)),
            negative_evidence_count=min(negative, 10000) if negative else None,
            last_reinforced_at=observed_at or datetime.now(timezone.utc),
        )

    def _vector_candidates(
        self,
        query: str,
        *,
        rows_by_event_id: dict[str, dict[str, Any]],
        client_id: str,
        user_id: str | None,
        task_id: str | None,
        skill_id: str | None,
        deliverable_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if self._vector_index is None:
            return []
        try:
            hits = self._vector_index.search(
                query,
                client_id=client_id,
                user_id=user_id,
                task_id=task_id,
                skill_id=skill_id,
                deliverable_type=deliverable_type,
                limit=limit,
            )
        except Exception as e:
            logger.warning("Hindsight vector recall 失败，将回退确定性检索: %s", e)
            return []
        out: list[dict[str, Any]] = []
        for hit in hits:
            row = rows_by_event_id.get(hit.event_id)
            if row is not None:
                out.append({**row, "_vector_distance": hit.distance})
        return out

    def search_lessons(
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
        debug_scores: bool = False,
        include_superseded: bool = True,
    ) -> list[str]:
        """检索反馈与教训：租户硬过滤 + supersedes 降权 + 可选同类合并/频次加权。"""
        if not self._path.is_file():
            return []
        qtokens = query_features(query)
        candidates: list[dict[str, Any]] = []
        rows_by_event_id: dict[str, dict[str, Any]] = {}
        for row in self._rows():
            if row.get("client_id") != client_id:
                continue
            if row.get("type") not in ("feedback", "lesson"):
                continue
            text = row.get("text") or ""
            if not isinstance(text, str) or not text:
                continue
            candidates.append(row)
            eid = _row_event_id(row)
            if eid:
                rows_by_event_id[eid] = row

        vector_candidates = self._vector_candidates(
            query,
            rows_by_event_id=rows_by_event_id,
            client_id=client_id,
            user_id=user_id,
            task_id=task_id,
            skill_id=skill_id,
            deliverable_type=deliverable_type,
            limit=max(_route_candidate_limit(limit), self._vector_candidate_limit),
        )
        routed_candidates = route_hindsight_candidates(
            candidates,
            query_terms=qtokens,
            user_id=user_id,
            task_id=task_id,
            skill_id=skill_id,
            deliverable_type=deliverable_type,
            max_rows=_route_candidate_limit(limit),
        )
        if vector_candidates:
            by_event_id: dict[str, dict[str, Any]] = {}
            union: list[dict[str, Any]] = []
            for row in vector_candidates:
                eid = _row_event_id(row)
                if eid:
                    by_event_id[eid] = row
                union.append(row)
            for row in routed_candidates:
                eid = _row_event_id(row)
                if eid and eid in by_event_id:
                    continue
                union.append(row)
            candidates = union
        else:
            candidates = routed_candidates

        superseded_ids: set[str] = set()
        for row in candidates:
            sid = row.get("supersedes_event_id")
            if isinstance(sid, str) and sid.strip():
                self_eid = _row_event_id(row)
                if self_eid and sid.strip() == self_eid:
                    continue
                superseded_ids.add(sid.strip())

        if not _freq_merge_enabled():
            scored: list[tuple[float, str, bool, str, str, str, str, str]] = []
            for row in candidates:
                is_superseded = _row_event_id(row) in superseded_ids
                if is_superseded and not include_superseded and not debug_scores:
                    continue
                sc = _explain_hindsight_row(
                    row,
                    qtokens=qtokens,
                    user_id=user_id,
                    task_id=task_id,
                    skill_id=skill_id,
                    deliverable_type=deliverable_type,
                    superseded=is_superseded,
                )
                sc = self._apply_vector_rerank(sc, row)
                rendered = _render_row(row, temporal_grounding=temporal_grounding)
                if debug_scores:
                    rendered = _render_debug(rendered, sc)
                scored.append(
                    (
                        sc.score,
                        rendered,
                        is_superseded,
                        _budget_cluster_key(row),
                        _metadata_key(row.get("user_id")),
                        _metadata_key(row.get("task_id")),
                        _metadata_key(row.get("skill_id")),
                        _metadata_key(row.get("deliverable_type")),
                    )
                )
            scored.sort(key=lambda x: -x[0])
            out: list[str] = []
            budget = _BudgetState(
                limit=limit,
                requested_user_id=user_id,
                requested_task_id=task_id,
                requested_skill_id=skill_id,
                requested_deliverable_type=deliverable_type,
            )
            for (
                _,
                t,
                is_superseded,
                cluster_key,
                row_user_key,
                row_task_key,
                skill_key,
                dtype_key,
            ) in scored:
                _append_with_budget(
                    out,
                    rendered=t,
                    superseded=is_superseded,
                    cluster_key=cluster_key,
                    user_key=row_user_key,
                    task_key=row_task_key,
                    skill_key=skill_key,
                    deliverable_key=dtype_key,
                    budget=budget,
                )
                if len(out) >= limit:
                    break
            if len(out) < limit:
                for _, t, *_ in scored:
                    _append_relaxed(out, rendered=t, budget=budget)
                    if len(out) >= limit:
                        break
            return out

        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in candidates:
            k = _merge_bucket_key(row)
            buckets.setdefault(k, []).append(row)

        merged_scored: list[tuple[float, str, bool, str, str, str, str, str]] = []
        for key, group in buckets.items():
            freq = len(group)
            total_w = sum(_row_weight(r) for r in group)
            best = max(
                group,
                key=lambda r: (
                    self._apply_vector_rerank(
                        _explain_hindsight_row(
                            r,
                            qtokens=qtokens,
                            user_id=user_id,
                            task_id=task_id,
                            skill_id=skill_id,
                            deliverable_type=deliverable_type,
                            superseded=_row_event_id(r) in superseded_ids,
                        ),
                        r,
                    ).score,
                    _recorded_epoch(r),
                ),
            )
            is_best_superseded = _row_event_id(best) in superseded_ids
            if is_best_superseded and not include_superseded and not debug_scores:
                continue
            base_score = _explain_hindsight_row(
                best,
                qtokens=qtokens,
                user_id=user_id,
                task_id=task_id,
                skill_id=skill_id,
                deliverable_type=deliverable_type,
                superseded=is_best_superseded,
            )
            base_score = self._apply_vector_rerank(base_score, best)
            bonus = min(4.0, math.log2(1.0 + float(freq)) * 1.25)
            if total_w > freq:
                bonus += min(2.0, 0.12 * float(total_w - freq))
            merged_sc = base_score.score + bonus
            reasons = list(base_score.reasons)
            if bonus:
                reasons.append(f"freq_bonus={bonus:.2f}")
            rendered = _render_row(best, temporal_grounding=temporal_grounding)
            if freq > 1 or total_w > freq:
                rendered += f" （同类×{freq}，总权重×{total_w}）"
            if debug_scores:
                rendered = _render_debug(rendered, HindsightScore(score=merged_sc, reasons=reasons))
            merged_scored.append(
                (
                    merged_sc,
                    rendered,
                    is_best_superseded,
                    _budget_cluster_key(best),
                    _metadata_key(best.get("user_id")),
                    _metadata_key(best.get("task_id")),
                    _metadata_key(best.get("skill_id")),
                    _metadata_key(best.get("deliverable_type")),
                )
            )

        merged_scored.sort(key=lambda x: -x[0])
        out2: list[str] = []
        budget = _BudgetState(
            limit=limit,
            requested_user_id=user_id,
            requested_task_id=task_id,
            requested_skill_id=skill_id,
            requested_deliverable_type=deliverable_type,
        )
        for (
            _,
            t,
            is_superseded,
            cluster_key,
            row_user_key,
            row_task_key,
            skill_key,
            dtype_key,
        ) in merged_scored:
            _append_with_budget(
                out2,
                rendered=t,
                superseded=is_superseded,
                cluster_key=cluster_key,
                user_key=row_user_key,
                task_key=row_task_key,
                skill_key=skill_key,
                deliverable_key=dtype_key,
                budget=budget,
            )
            if len(out2) >= limit:
                break
        if len(out2) < limit:
            for _, t, *_ in merged_scored:
                _append_relaxed(out2, rendered=t, budget=budget)
                if len(out2) >= limit:
                    break
        return out2
