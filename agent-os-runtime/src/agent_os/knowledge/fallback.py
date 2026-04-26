from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KnowledgeJsonlFallback:
    """
    无 Neo4j / 超时 / Graphiti 异常时的文本降级检索。
    每行 JSON：`{"group_id": "...", "text": "..."}`，group_id 须与
    ``agent_os.knowledge.group_id.system_graphiti_group_id(skill_id)`` 一致。
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lines: list[dict[str, str]] = []
        if path and path.is_file():
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("知识降级 JSONL 无法读取，禁用 fallback: %s (%s)", path, e)
                lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("跳过无效 JSON 行: %s", line[:80])
                    continue
                if not isinstance(row, dict):
                    logger.warning("跳过非对象 JSON 行: %s", line[:80])
                    continue
                if not isinstance(row.get("group_id"), str) or not isinstance(row.get("text"), str):
                    logger.warning("跳过字段类型不合法的 JSON 行: %s", line[:80])
                    continue
                self._lines.append(row)

    @property
    def enabled(self) -> bool:
        return bool(self._path and self._path.is_file() and self._lines)

    def search(self, query: str, group_id: str, limit: int = 8) -> str:
        if not self._lines:
            return ""
        qtokens = set(query.lower().split())
        scored: list[tuple[int, str]] = []
        for row in self._lines:
            if row.get("group_id") != group_id:
                continue
            text = row.get("text", "")
            if not isinstance(text, str):
                continue
            if not text:
                continue
            tokens = set(text.lower().split())
            score = len(qtokens & tokens) if qtokens else 1
            scored.append((score, text))
        scored.sort(key=lambda x: -x[0])
        top = [t for _, t in scored[:limit]]
        if not top:
            return ""
        return "\n---\n".join(top)
