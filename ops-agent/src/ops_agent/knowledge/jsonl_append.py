from __future__ import annotations

import json
from pathlib import Path

from ops_agent.knowledge.group_id import graphiti_group_id


def append_knowledge_lines(
    path: Path, client_id: str, texts: list[str], *, skill_id: str = "default_ops"
) -> int:
    """
    向 OPS_KNOWLEDGE_FALLBACK_PATH 格式的 JSONL 追加行（group_id + text）。
    ``group_id`` 使用 ``graphiti_group_id(client_id, skill_id)``，须与 Graphiti 检索一致。
    若文件不存在则创建父目录。
    """
    gid = graphiti_group_id(client_id, skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for t in texts:
            line = json.dumps({"group_id": gid, "text": t.strip()}, ensure_ascii=False)
            f.write(line + "\n")
            n += 1
    return n
