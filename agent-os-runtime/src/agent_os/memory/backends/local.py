from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

from agent_os.memory.models import MemorySearchHit

logger = logging.getLogger(__name__)


class LocalMemoryBackend:
    """无 MEM0_API_KEY 时使用：JSON 文件 + 简单子串匹配检索，用于开发与 CI。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            self._data: dict[str, Any] = {"users": {}}
            return self._data
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("本地记忆文件无法解析，使用空内存视图: %s", e)
            data = {}
        if not isinstance(data, dict):
            logger.warning("本地记忆文件根节点不是对象，使用空内存视图: %s", self._path)
            data = {}
        users = data.get("users")
        if not isinstance(users, dict):
            data["users"] = {}
        else:
            for uid, bucket in list(users.items()):
                if not isinstance(bucket, dict):
                    users[uid] = {"memories": []}
                    continue
                memories = bucket.get("memories")
                if not isinstance(memories, list):
                    bucket["memories"] = []
        self._data = data
        return self._data

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def mem_user_id(client_id: str, user_id: str | None) -> str:
        if user_id:
            return f"{client_id}::{user_id}"
        return client_id

    def add_messages(
        self,
        *,
        messages: list[dict[str, str]],
        client_id: str,
        user_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        uid = self.mem_user_id(client_id, user_id)
        users = self._data["users"]
        bucket = users.setdefault(uid, {"memories": []})
        text = "\n".join(m.get("content", "") for m in messages)
        entry = {"text": text, "metadata": metadata}
        bucket["memories"].append(entry)
        self._save()
        return {"status": "ok", "local": True, "user_id": uid}

    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        limit: int = 8,
    ) -> List[MemorySearchHit]:
        uid = self.mem_user_id(client_id, user_id)
        users = self._data.get("users", {})
        bucket = users.get(uid, {"memories": []})
        q = query.lower()
        hits: List[MemorySearchHit] = []
        if not isinstance(bucket, dict):
            return []
        memories = bucket.get("memories", [])
        if not isinstance(memories, list):
            return []
        for m in reversed(memories):
            if not isinstance(m, dict):
                continue
            text = m.get("text", "")
            if not isinstance(text, str):
                continue
            if q in text.lower() or not q:
                hits.append(MemorySearchHit(text=text, metadata=m.get("metadata") or {}))
            if len(hits) >= limit:
                break
        return hits

    def snapshot_client_profile(self, client_id: str, user_id: str | None) -> None:
        logger.info("本地快照已写入: %s", self._path)
