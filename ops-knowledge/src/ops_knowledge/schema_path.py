from __future__ import annotations

import os
from pathlib import Path


def resolve_lesson_merged_schema_path(explicit: Path | None) -> Path:
    """解析 lesson_merged.schema.json 路径。"""
    if explicit is not None:
        p = explicit.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Schema 不存在: {p}")
        return p
    root = os.getenv("VIDEO_RAW_INGEST_ROOT")
    if not root:
        raise ValueError(
            "请设置环境变量 VIDEO_RAW_INGEST_ROOT 指向 ① 仓库根目录（默认目录名见 ops-stack/ops-stack.toml 中 "
            "repos.video_raw_ingest），或使用 --schema 指定 lesson_merged.schema.json 路径。"
        )
    p = Path(root).expanduser().resolve() / "schema" / "lesson_merged.schema.json"
    if not p.is_file():
        raise FileNotFoundError(f"未找到 schema 文件: {p}（请检查 VIDEO_RAW_INGEST_ROOT）")
    return p
