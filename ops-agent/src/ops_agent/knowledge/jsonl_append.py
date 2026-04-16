from __future__ import annotations

import json
from pathlib import Path

from ops_agent.knowledge.group_id import sanitize_group_id


def append_knowledge_lines(path: Path, client_id: str, texts: list[str]) -> int:
    """
    向 OPS_KNOWLEDGE_FALLBACK_PATH 格式的 JSONL 追加行（group_id + text）。
    若文件不存在则创建父目录。
    """
    gid = sanitize_group_id(client_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for t in texts:
            line = json.dumps({"group_id": gid, "text": t.strip()}, ensure_ascii=False)
            f.write(line + "\n")
            n += 1
    return n
