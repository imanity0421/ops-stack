from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_os.memory.models import UserFact

logger = logging.getLogger(__name__)


class HindsightStore:
    """
    任务反馈与复盘教训的本地存储（JSONL）。
    每行 JSON 至少含：type, client_id, text；type 为 feedback | lesson。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append_feedback(self, fact: UserFact) -> dict[str, Any]:
        line = {
            "type": "feedback",
            "client_id": fact.client_id,
            "user_id": fact.user_id,
            "task_id": fact.task_id,
            "deliverable_type": fact.deliverable_type,
            "text": fact.text,
            "impact_on_preference": fact.impact_on_preference,
            "recorded_at": fact.recorded_at.isoformat(),
            "source": fact.source,
        }
        self._append_line(line)
        return {"status": "ok", "path": str(self._path)}

    def append_lesson(
        self,
        *,
        client_id: str,
        text: str,
        user_id: str | None = None,
        task_id: str | None = None,
        source: str = "async_review",
    ) -> dict[str, Any]:
        line = {
            "type": "lesson",
            "client_id": client_id,
            "user_id": user_id,
            "task_id": task_id,
            "text": text.strip(),
            "source": source,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append_line(line)
        return {"status": "ok", "path": str(self._path)}

    def _append_line(self, obj: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def search_lessons(
        self,
        query: str,
        client_id: str,
        limit: int = 8,
        *,
        temporal_grounding: bool = True,
    ) -> list[str]:
        """检索反馈与教训（Hindsight），按简单词重叠排序。"""
        if not self._path.is_file():
            return []
        qtokens = set(query.lower().split())
        scored: list[tuple[int, str]] = []
        for line in self._path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("client_id") != client_id:
                continue
            if row.get("type") not in ("feedback", "lesson"):
                continue
            text = row.get("text") or ""
            if not isinstance(text, str):
                continue
            if not text:
                continue
            recorded = row.get("recorded_at") or row.get("created_at") or "记录时间未知"
            source = row.get("source") or "unknown"
            rendered = (
                f"[记录于 {recorded} | 来源 {source}] {text}" if temporal_grounding else str(text)
            )
            tokens = set(text.lower().split())
            score = len(qtokens & tokens) if qtokens else 1
            scored.append((score, rendered))
        scored.sort(key=lambda x: -x[0])
        out: list[str] = []
        seen: set[str] = set()
        for _, t in scored:
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= limit:
                break
        return out
