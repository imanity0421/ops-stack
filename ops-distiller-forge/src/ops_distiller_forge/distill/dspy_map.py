from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ops_distiller_forge.config import ForgeSettings
from ops_distiller_forge.ontology.models import KnowledgePoint, LineageMeta


def map_with_dspy(
    raw: dict[str, Any],
    *,
    merged_path: Path,
    source_relpath: str,
    settings: ForgeSettings,
) -> list[KnowledgePoint]:
    """使用 DSPy 从 merged JSON 生成结构化知识点（需 dspy-ai + OPENAI_API_KEY）。"""
    import dspy

    speech_text = _speech_text(raw)

    class ExtractKP(dspy.Signature):
        """从课程转写中抽取一条核心运营知识点，用于私域/SaaS 运营手册。"""

        transcript: str = dspy.InputField(desc="课程语音转写全文")
        title: str = dspy.OutputField(desc="知识点短标题")
        theory_logic: str = dspy.OutputField(desc="为什么这么做，因果与边界")
        sop_steps: str = dspy.OutputField(desc="分号或换行分隔的可执行步骤")
        key_metrics: str = dspy.OutputField(desc="可量化指标，逗号分隔")
        anti_patterns: str = dspy.OutputField(desc="常见错误，逗号分隔")

    kwargs: dict[str, str] = {}
    if settings.openai_api_base:
        kwargs["api_base"] = settings.openai_api_base
    lm = dspy.LM(settings.dspy_lm_model, **kwargs)
    dspy.configure(lm=lm)
    prog = dspy.ChainOfThought(ExtractKP)
    pred = prog(transcript=speech_text[:120_000])

    steps = [s.strip() for s in _split_steps(pred.sop_steps)]
    metrics = [x.strip() for x in pred.key_metrics.replace("，", ",").split(",") if x.strip()]
    anti = [x.strip() for x in pred.anti_patterns.replace("，", ",").split(",") if x.strip()]

    digest = __import__("hashlib").sha256(speech_text.encode("utf-8")).hexdigest()[:16]
    meta = LineageMeta(
        source_relpath=source_relpath,
        source_sha256=digest,
        handbook_version=settings.default_handbook_version,
        pipeline_version="0.1.0",
        lesson_id=raw.get("video", {}).get("path") if isinstance(raw.get("video"), dict) else None,
    )
    kp = KnowledgePoint(
        id=str(uuid.uuid4()),
        title=(pred.title or merged_path.stem)[:200],
        theory_logic=pred.theory_logic.strip(),
        sop_steps=steps[:30],
        key_metrics=metrics[:20],
        anti_patterns=anti[:20],
        case_reference=[],
        metadata=meta,
        cluster_key=None,
    )
    return [kp]


def _speech_text(raw: dict[str, Any]) -> str:
    speech = raw.get("speech") or {}
    segments = speech.get("segments") or []
    lines: list[str] = []
    for seg in segments:
        if isinstance(seg, dict) and seg.get("text"):
            lines.append(str(seg["text"]).strip())
    return "\n".join(lines).strip()


def _split_steps(s: str) -> list[str]:
    s = s.strip()
    if not s:
        return []
    for sep in ["\n", "；", ";"]:
        if sep in s:
            return [x.strip() for x in s.split(sep) if x.strip()]
    return [s]
