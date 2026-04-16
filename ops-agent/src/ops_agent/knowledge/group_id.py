from __future__ import annotations

import re

# 与 graphiti_core.helpers.validate_group_id 对齐：^[a-zA-Z0-9_-]+$
_SAFE = re.compile(r"^[a-zA-Z0-9_-]+$")


def sanitize_group_id(client_id: str) -> str:
    """
    将业务侧 client_id 映射为 Graphiti group_id（与入库时的 partition 一致）。
    非法字符替换为下划线；若为空则使用 'default'。
    """
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", client_id.strip())
    s = s.strip("_") or "default"
    if not _SAFE.match(s):
        s = "client_" + re.sub(r"[^a-zA-Z0-9_-]", "_", client_id)[:64]
    return s
