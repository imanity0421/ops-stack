"""健全性校验：JSON Schema + 硬规则，输出 validation_report.json。"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def _load_schema() -> dict[str, Any]:
    name = "lesson_merged.schema.json"
    pkg = resources.files("video_raw_ingest")
    try:
        text = pkg.joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        here = Path(__file__).resolve().parent
        fallback = here.parent.parent.parent / "schema" / name
        text = fallback.read_text(encoding="utf-8")
    return json.loads(text)


def validate_merged(
    merged: dict[str, Any],
    out_root: Path,
    *,
    require_speech: bool = False,
    require_visual_text: bool = False,
) -> dict[str, Any]:
    """
    返回 validation_report 字典；errors 非空即硬失败（由 CLI 决定退出码）。
    """
    errors: list[str] = []
    warnings: list[str] = []

    schema = _load_schema()
    try:
        Draft202012Validator(schema).validate(merged)
    except Exception as e:
        errors.append(f"schema: {e}")

    ver = merged.get("schema_version")
    if ver != "1.0":
        warnings.append(f"schema_version 非 1.0: {ver!r}")

    video_path = (merged.get("video") or {}).get("path")
    if video_path and not Path(video_path).is_file():
        warnings.append(f"源视频路径当前不可访问（可能已移动）: {video_path}")

    dur = (merged.get("video") or {}).get("duration_sec")
    if isinstance(dur, (int, float)) and dur <= 0:
        errors.append("duration_sec 无效")

    speech = merged.get("speech") or {}
    segs = speech.get("segments") or []
    if require_speech and len(segs) == 0:
        errors.append("require_speech：口播片段为空")

    t_prev = -1.0
    for i, seg in enumerate(segs):
        try:
            a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
        except (TypeError, ValueError):
            errors.append(f"speech.segments[{i}] 时间戳非数字")
            continue
        if a > b:
            errors.append(f"speech.segments[{i}] start > end")
        if a < t_prev:
            warnings.append(f"speech.segments[{i}] 未按时间单调（相对前段）")
        t_prev = max(t_prev, b)

    slides = (merged.get("visual") or {}).get("slides") or []
    for i, s in enumerate(slides):
        rel = s.get("frame_relpath")
        if rel:
            p = out_root / str(rel)
            if not p.is_file():
                errors.append(f"visual.slides[{i}] 帧文件不存在: {p}")

    if require_visual_text:
        any_text = any((s.get("mineru_markdown") or "").strip() for s in slides)
        if not any_text:
            errors.append("require_visual_text：无 MinerU 正文")

    # 时间线粗检
    tl = (merged.get("merged") or {}).get("timeline") or []
    for i, ev in enumerate(tl):
        try:
            float(ev.get("start_sec", 0.0))
        except (TypeError, ValueError):
            errors.append(f"merged.timeline[{i}] start_sec 无效")

    status = "failed" if errors else "ok"
    report = {
        "status": status,
        "errors": errors,
        "warnings": warnings,
    }
    return report


def write_validation_report(report: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def slugify_hint(name: str, max_len: int = 80) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-_.]+", "_", name.strip())
    return s[:max_len] if len(s) > max_len else s
