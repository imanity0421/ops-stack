from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def distill_stub_from_merged(merged_path: Path) -> dict[str, Any]:
    """
    无 LLM 的占位「蒸馏」：从 lesson_merged.json 抽取可展示片段，供管线联调。
    真实 DSPy 蒸馏上线后应替换本函数输出。
    """
    data = json.loads(merged_path.read_text(encoding="utf-8"))
    speech = data.get("speech") or {}
    segments = speech.get("segments") or []
    preview = ""
    if segments and isinstance(segments[0], dict):
        preview = str(segments[0].get("text", ""))[:400]
    merged = data.get("merged") or {}
    tl = merged.get("timeline") or []
    return {
        "stub": True,
        "source": str(merged_path),
        "schema_version": data.get("schema_version"),
        "speech_preview": preview,
        "timeline_events": len(tl) if isinstance(tl, list) else 0,
        "next_steps": [
            "接入 dspy-ai + LLM 生成运营手册条目",
            "ops-knowledge manifest → OPS_HANDOFF_MANIFEST_PATH",
        ],
    }


def write_distill_stub(merged_path: Path, output_path: Path) -> None:
    out = distill_stub_from_merged(merged_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
