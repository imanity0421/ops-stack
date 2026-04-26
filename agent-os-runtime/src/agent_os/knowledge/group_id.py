from __future__ import annotations

import re

# 与 graphiti_core.helpers.validate_group_id 对齐：^[a-zA-Z0-9_-]+$
_SAFE = re.compile(r"^[a-zA-Z0-9_-]+$")

# 复合分区键中 client 段与 skill 段的分隔（双下划线，避免与业务 id 中单 _ 混淆）
_COMPOSITE_SEP = "__"


def sanitize_group_id(segment: str) -> str:
    """
    将任意业务段（client_id、skill_id 或历史意义上的单租户键）清洗为 Graphiti 合法片段。

    复合分区请使用 ``graphiti_group_id(client_id, skill_id)``，须与入库、JSONL fallback 一致。
    """
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", segment.strip())
    s = s.strip("_") or "default"
    if not _SAFE.match(s):
        s = "seg_" + re.sub(r"[^a-zA-Z0-9_-]", "_", segment)[:64]
    return s


def graphiti_group_id(client_id: str, skill_id: str) -> str:
    """
    Graphiti / JSONL 领域知识分区：租户 × skill，防止不同 skill 的 Episode 串味。

    形如 ``{sanitize(client)}__{sanitize(skill)}``；须与 ``graphiti_ingest``、
    ``GraphitiReadService``、``append_knowledge_lines`` 使用同一函数。
    """
    a = sanitize_group_id(client_id)
    b = sanitize_group_id(skill_id)
    return f"{a}{_COMPOSITE_SEP}{b}"


def system_graphiti_group_id(skill_id: str) -> str:
    """
    系统级干净知识图谱分区：仅按 skill/domain 分区。

    ``client_id`` 不再表达客户自有图谱；调用方应在检索前做权限过滤。
    """
    return sanitize_group_id(skill_id)
