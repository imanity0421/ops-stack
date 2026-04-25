from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_handoff_instruction_lines(manifest_path: Path | None) -> list[str]:
    """
    从 handbook_handoff.json 生成可追加到 Agent 指令的短句（无文件或解析失败则返回空列表）。
    """
    if manifest_path is None or not manifest_path.is_file():
        return []
    try:
        raw = manifest_path.read_text(encoding="utf-8-sig")
        data: Any = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return ["【制品清单】handbook_handoff.json 无法解析，已忽略。"]

    if not isinstance(data, dict):
        return []

    ver = data.get("handoff_version", "?")
    created = data.get("created_utc", "")
    schema_ref = data.get("video_raw_ingest_schema_ref", "")
    lessons = data.get("lessons")
    if not isinstance(lessons, list):
        lessons = []

    valid_n = sum(1 for x in lessons if isinstance(x, dict) and x.get("valid") is True)
    invalid_n = sum(1 for x in lessons if isinstance(x, dict) and x.get("valid") is False)
    n = len(lessons)

    parts = [
        f"【制品清单】handoff_version={ver}，条目数={n}，校验通过={valid_n}，未通过={invalid_n}。"
    ]
    if created:
        parts.append(f"清单生成时间（UTC）：{created}。")
    if schema_ref:
        parts.append(f"关联 schema：{schema_ref}。")
    return parts
