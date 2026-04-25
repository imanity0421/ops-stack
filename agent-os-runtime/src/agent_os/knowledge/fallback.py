from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KnowledgeJsonlFallback:
    """
    无 Neo4j / 超时 / Graphiti 异常时的文本降级检索。
    每行 JSON：`{"group_id": "...", "text": "..."}`，group_id 须与
    ``agent_os.knowledge.group_id.graphiti_group_id(client_id, skill_id)`` 一致。
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lines: list[dict[str, str]] = []
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._lines.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("跳过无效 JSON 行: %s", line[:80])

    @property
    def enabled(self) -> bool:
        return bool(self._path and self._path.exists() and self._lines)

    def search(self, query: str, group_id: str, limit: int = 8) -> str:
        if not self._lines:
            return ""
        qtokens = set(query.lower().split())
        scored: list[tuple[int, str]] = []
        for row in self._lines:
            if row.get("group_id") != group_id:
                continue
            text = row.get("text", "")
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
