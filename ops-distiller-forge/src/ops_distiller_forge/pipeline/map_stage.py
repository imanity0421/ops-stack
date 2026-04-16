from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from ops_distiller_forge.config import ForgeSettings
from ops_distiller_forge.ontology.models import KnowledgePoint, LineageMeta


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_speech_text(data: dict[str, Any]) -> str:
    speech = data.get("speech") or {}
    segments = speech.get("segments") or []
    lines: list[str] = []
    for seg in segments:
        if isinstance(seg, dict) and seg.get("text"):
            lines.append(str(seg["text"]).strip())
    return "\n".join(lines).strip()


def map_lesson_merged(
    merged_path: Path,
    *,
    settings: ForgeSettings | None = None,
    source_relpath: str | None = None,
    use_dspy: bool = False,
) -> list[KnowledgePoint]:
    """
    Map：单课 lesson_merged.json → 一条或多条 KnowledgePoint。

    - 默认 **deterministic**（无 API）：从转写拼接正文，拆成步骤占位，便于 CI。
    - ``use_dspy=True`` 且已安装 ``dspy-ai``、配置 ``OPENAI_API_KEY``：调用 DSPy 签名生成 richer 字段。
    """
    settings = settings or ForgeSettings.from_env()
    raw = json.loads(merged_path.read_text(encoding="utf-8"))
    rel = source_relpath or merged_path.name
    speech = _extract_speech_text(raw)
    digest = _text_sha256(speech or json.dumps(raw, sort_keys=True))[:16]

    if use_dspy and settings.openai_api_key:
        try:
            from ops_distiller_forge.distill.dspy_map import map_with_dspy

            return map_with_dspy(raw, merged_path=merged_path, source_relpath=rel, settings=settings)
        except Exception:
            pass

    return _map_deterministic(raw, speech, rel, digest, settings)


def _map_deterministic(
    raw: dict[str, Any],
    speech: str,
    source_relpath: str,
    digest: str,
    settings: ForgeSettings,
) -> list[KnowledgePoint]:
    title = "课程要点摘要"
    if speech:
        first_line = speech.split("\n")[0].strip()
        if len(first_line) > 80:
            title = first_line[:80] + "…"
        else:
            title = first_line or title

    steps: list[str] = []
    for line in speech.split("\n"):
        line = line.strip()
        if len(line) > 10 and len(steps) < 12:
            steps.append(line)

    kp_id = str(uuid.uuid4())
    meta = LineageMeta(
        source_relpath=source_relpath,
        source_sha256=digest,
        handbook_version=settings.default_handbook_version,
        pipeline_version="0.1.0",
        lesson_id=raw.get("video", {}).get("path") if isinstance(raw.get("video"), dict) else None,
    )
    theory = speech[:8000] if speech else "（无语音转写文本）"
    kp = KnowledgePoint(
        id=kp_id,
        title=title,
        theory_logic=theory,
        sop_steps=steps[:20] if steps else ["通读全文并提炼可执行清单"],
        key_metrics=["完课率", "互动率"],
        anti_patterns=["空话套话堆砌", "无数据支撑的承诺"],
        case_reference=[],
        metadata=meta,
        cluster_key=None,
    )
    return [kp]
