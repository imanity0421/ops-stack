from __future__ import annotations

import json
from pathlib import Path

from agent_os.knowledge.group_id import system_graphiti_group_id


def append_knowledge_lines(
    path: Path, client_id: str, texts: list[str], *, skill_id: str = "default_agent"
) -> int:
    """
    向 AGENT_OS_KNOWLEDGE_FALLBACK_PATH 格式的 JSONL 追加行（group_id + text）。
    ``group_id`` 使用系统级 ``system_graphiti_group_id(skill_id)``，须与 Graphiti 检索一致。
    ``client_id`` 保留在签名中用于 CLI 兼容，不参与系统知识分区。
    若文件不存在则创建父目录。
    """
    _ = client_id
    gid = system_graphiti_group_id(skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for t in texts:
            text = t.strip()
            if not text:
                continue
            line = json.dumps({"group_id": gid, "text": text}, ensure_ascii=False)
            f.write(line + "\n")
            n += 1
    return n
