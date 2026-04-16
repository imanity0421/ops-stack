"""结构合并：口播片段 + 画面解析，按时间线排序（无语义重写）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__


def build_merged(
    *,
    video_path: Path,
    duration_sec: float | None,
    probe_summary: dict[str, Any],
    speech: dict[str, Any],
    slides: list[dict[str, Any]],
) -> dict[str, Any]:
    segments = speech.get("segments") or []
    speech_empty = len(segments) == 0
    visual_empty = len(slides) == 0 or all(
        not (s.get("mineru_markdown") or "").strip() for s in slides
    )

    timeline: list[dict[str, Any]] = []

    for seg in segments:
        timeline.append(
            {
                "kind": "speech",
                "source": "whisperx",
                "start_sec": float(seg.get("start", 0.0)),
                "end_sec": float(seg.get("end", 0.0)),
                "text": (seg.get("text") or "").strip(),
            }
        )

    for s in slides:
        t0 = float(s.get("timestamp_sec", 0.0))
        md = (s.get("mineru_markdown") or "").strip()
        timeline.append(
            {
                "kind": "visual",
                "source": "mineru",
                "start_sec": t0,
                "end_sec": t0,
                "text": md,
                "frame_relpath": s.get("frame_relpath"),
                "mineru_output_dir": s.get("mineru_output_dir"),
                "mineru_error": s.get("mineru_error"),
            }
        )

    timeline.sort(key=lambda x: (x.get("start_sec", 0.0), x.get("kind") == "speech"))

    return {
        "schema_version": "1.0",
        "pipeline_version": __version__,
        "video": {
            "path": str(video_path),
            "duration_sec": duration_sec,
            "probe_summary": probe_summary,
        },
        "speech": {
            **speech,
            "empty": speech_empty,
        },
        "visual": {
            "slides": slides,
            "empty": visual_empty,
        },
        "flags": {
            "speech_empty": speech_empty,
            "visual_empty": visual_empty,
        },
        "merged": {
            "timeline": timeline,
        },
    }


def write_merged_markdown(merged: dict[str, Any], path: Path) -> None:
    """人类可读并列视图，非语义合并。"""
    lines: list[str] = []
    v = merged.get("video") or {}
    lines.append(f"# {Path(v.get('path', 'lesson')).name}")
    lines.append("")
    lines.append("## 元数据")
    lines.append("")
    lines.append(f"- 源视频: `{v.get('path')}`")
    if v.get("duration_sec") is not None:
        lines.append(f"- 时长: {v['duration_sec']:.1f} s")
    lines.append("")

    lines.append("## 口播（WhisperX）")
    lines.append("")
    for seg in (merged.get("speech") or {}).get("segments") or []:
        t0 = float(seg.get("start", 0.0))
        t1 = float(seg.get("end", 0.0))
        text = (seg.get("text") or "").strip()
        lines.append(f"- **[{t0:.2f} – {t1:.2f}s]** {text}")
    lines.append("")

    lines.append("## 画面（MinerU）")
    lines.append("")
    for s in (merged.get("visual") or {}).get("slides") or []:
        ts = float(s.get("timestamp_sec", 0.0))
        lines.append(f"### [{ts:.2f}s] 帧 {s.get('index')}")
        lines.append("")
        err = s.get("mineru_error")
        if err:
            lines.append(f"_(MinerU 错误: {err})_")
            lines.append("")
        md = (s.get("mineru_markdown") or "").strip()
        lines.append(md if md else "_(无文本)_")
        lines.append("")

    lines.append("## 时间线（结构合并，未改写）")
    lines.append("")
    for ev in (merged.get("merged") or {}).get("timeline") or []:
        k = ev.get("kind")
        t0 = float(ev.get("start_sec", 0.0))
        if k == "speech":
            t1 = float(ev.get("end_sec", 0.0))
            lines.append(f"- **[speech {t0:.2f}-{t1:.2f}s]** {ev.get('text','')}")
        else:
            lines.append(f"- **[visual {t0:.2f}s]** {(ev.get('text') or '')[:500]}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_merged_json(merged: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
