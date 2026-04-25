from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import OpenAI

from agent_os.knowledge.asset_store import (
    AssetCase,
    AssetStatus,
    AssetStore,
    compute_content_hash,
    compute_dedup_key,
)
from agent_os.knowledge.skill_compliance import check_skill_compliance

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestOptions:
    client_id: str
    user_id: str | None
    skill_id: str
    source: str | None = None
    model: str = "gpt-4o-mini"
    max_raw_chars: int = 20000
    min_raw_chars: int = 200
    allow_llm: bool = True
    #: 每 skill 合规规则目录（``AGENT_OS_SKILL_COMPLIANCE_DIR``）；未设置则不做硬合规
    compliance_dir: Path | None = None


def _basic_validate(raw: str, *, opt: IngestOptions) -> tuple[bool, str | None]:
    t = (raw or "").strip()
    if len(t) < opt.min_raw_chars:
        return False, "too_short"
    if len(t) > opt.max_raw_chars:
        return False, "too_long"
    # 简单乱码/无效文本信号
    if re.search(r"[\uFFFD]", t):
        return False, "contains_replacement_char"
    if len(set(t)) <= 8:
        return False, "low_entropy"
    return True, None


def _openai_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE") or None,
    )


def _llm_gatekeeper(raw: str, *, opt: IngestOptions) -> tuple[AssetStatus, str | None]:
    """
    裁判：判定是否可作为“语感参考案例”入库。
    - accepted：可直接入库
    - quarantined：可疑，建议人工复核
    - rejected：明显无效/风险/不成案
    """

    if not opt.allow_llm:
        return "accepted", None

    prompt = (
        "你是参考案例库的入库裁判。判断下面文本是否是“可复用的完整案例”（例如完整方案、完整说明、完整复盘），"
        "用于大模型模仿语感与结构。输出严格 JSON："
        '{"status":"accepted|quarantined|rejected","reason":"...简短原因..."}\n'
        "判定要点：是否成案完整、是否大量缺失上下文、是否明显乱码/广告垃圾/敏感风险、是否过短。\n\n"
        f"文本：\n{raw[:12000]}"
    )
    r = _openai_client().chat.completions.create(
        model=opt.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = (r.choices[0].message.content or "").strip()
    try:
        obj = json.loads(content)
        status = obj.get("status")
        reason = obj.get("reason")
        if status in ("accepted", "quarantined", "rejected"):
            return status, str(reason or "").strip() or None
    except Exception:
        pass
    return "quarantined", "gatekeeper_unparseable"


def _llm_extract_features(raw: str, *, opt: IngestOptions) -> dict[str, Any]:
    if not opt.allow_llm:
        # 无 LLM 时仅给最小可检索字段，避免 ingestion 失败（质量后续再补）
        return {
            "summary": raw[:400],
            "style_fingerprint": "（未抽取：allow_llm=0）",
            "tags": [],
            "platform": None,
            "content_type": None,
            "duration_bucket": None,
            "key_excerpts": [],
        }

    prompt = (
        "你是参考案例库的特征提取器。对下面整案案例抽取用于“语感检索”的特征。输出严格 JSON，字段：\n"
        "{\n"
        '  "summary": "高密度摘要（1段）",\n'
        '  "style_fingerprint": "风格指纹：语气/节奏/结构/表达方式/句式偏好等（1段）",\n'
        '  "tags": ["若干短标签"],\n'
        '  "platform": "交付场景或使用环境，如 chat|doc|web|api|report|code|other|unknown",\n'
        '  "content_type": "内容类型，如 方案|说明|复盘|清单|报告|代码|其他|unknown",\n'
        '  "duration_bucket": "长度区间，如 short|medium|long|unknown",\n'
        '  "key_excerpts": ["关键片段1","关键片段2","关键片段3"]\n'
        "}\n"
        "要求：key_excerpts 选最能体现写法的片段；不要输出多余字段。\n\n"
        f"文本：\n{raw[:12000]}"
    )
    r = _openai_client().chat.completions.create(
        model=opt.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = (r.choices[0].message.content or "").strip()
    return json.loads(content)


def ingest_text(raw: str, *, store: AssetStore, opt: IngestOptions) -> dict[str, Any]:
    ok, why = _basic_validate(raw, opt=opt)
    if not ok:
        return {"status": "rejected", "reason": why}

    ch = compute_content_hash(raw)
    dk = compute_dedup_key(opt.client_id, opt.skill_id, opt.user_id, ch)
    existing = store.find_case_id_by_dedup_key(dk)
    if existing:
        return {"status": "duplicate_skip", "reason": "exact_dedup", "case_id": existing}

    comp = check_skill_compliance(raw, opt.skill_id, opt.compliance_dir)
    if comp:
        return {"status": "rejected", "reason": "compliance", "violations": comp}

    status, reason = _llm_gatekeeper(raw, opt=opt)
    if status == "rejected":
        return {"status": "rejected", "reason": reason}

    try:
        feat = _llm_extract_features(raw, opt=opt)
        summary = str(feat.get("summary") or "").strip()
        style = str(feat.get("style_fingerprint") or "").strip()
        tags = feat.get("tags") or []
        key_excerpts = feat.get("key_excerpts") or []
        platform = feat.get("platform")
        content_type = feat.get("content_type")
        duration_bucket = feat.get("duration_bucket")
    except Exception as e:
        logger.warning("feature extract failed: %s", e)
        status = "quarantined"
        reason = "feature_extract_failed"
        summary = raw[:400].strip()
        style = "（特征抽取失败，需人工复核）"
        tags = []
        key_excerpts = []
        platform = None
        content_type = None
        duration_bucket = None

    if not summary or not style:
        status = "quarantined"
        reason = reason or "missing_summary_or_style"

    retrieval_text = "\n".join(
        [
            f"摘要：{summary}",
            f"风格：{style}",
            f"标签：{', '.join(tags) if isinstance(tags, list) else ''}",
            f"场景：{platform or ''} 类型：{content_type or ''} 长度：{duration_bucket or ''}",
        ]
    ).strip()

    near_max = (os.getenv("AGENT_OS_ASSET_NEAR_DEDUP_L2_MAX") or "").strip()
    if near_max and status == "accepted":
        try:
            near_id = store.find_near_duplicate_case_id(
                retrieval_text,
                client_id=opt.client_id,
                user_id=opt.user_id,
                skill_id=opt.skill_id,
                l2_max=float(near_max),
            )
            if near_id:
                return {"status": "duplicate_skip", "reason": "near_dedup", "case_id": near_id}
        except (TypeError, ValueError) as e:
            logger.debug("near dedup skipped: %s", e)

    case = AssetCase(
        case_id=str(uuid4()),
        client_id=opt.client_id,
        user_id=opt.user_id,
        skill_id=opt.skill_id,
        source=opt.source,
        raw_content=raw.strip(),
        summary=summary,
        style_fingerprint=style,
        key_excerpts=list(key_excerpts) if isinstance(key_excerpts, list) else [],
        tags=list(tags) if isinstance(tags, list) else [],
        platform=str(platform) if platform not in (None, "unknown") else None,
        content_type=str(content_type) if content_type not in (None, "unknown") else None,
        duration_bucket=str(duration_bucket) if duration_bucket not in (None, "unknown") else None,
        retrieval_text=retrieval_text,
        content_hash=ch,
        dedup_key=dk,
        status=status,
        reject_reason=reason if status != "accepted" else None,
    )

    if status == "quarantined":
        # MVP：quarantine 也允许写入（但 status != accepted，不参与检索），便于后续人工处理
        pass

    r = store.upsert_many([case])
    return {
        "status": "ok",
        "store": r,
        "case": {"case_id": case.case_id, "status": case.status, "reason": reason},
    }


def ingest_jsonl(path: Path, *, store: AssetStore, opt: IngestOptions) -> dict[str, Any]:
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    total = 0
    accepted = 0
    quarantined = 0
    rejected = 0
    reasons: dict[str, int] = {}

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            row = json.loads(line)
        except Exception:
            rejected += 1
            reasons["invalid_json"] = reasons.get("invalid_json", 0) + 1
            continue
        if not isinstance(row, dict):
            rejected += 1
            reasons["invalid_record"] = reasons.get("invalid_record", 0) + 1
            continue
        text = str(row.get("raw_content") or row.get("text") or "").strip()
        if not text:
            rejected += 1
            reasons["missing_text"] = reasons.get("missing_text", 0) + 1
            continue

        r = ingest_text(text, store=store, opt=opt)
        st = r.get("status")
        if st == "duplicate_skip":
            reasons["duplicate_skip"] = reasons.get("duplicate_skip", 0) + 1
            continue
        if st == "rejected":
            rejected += 1
            reasons[str(r.get("reason") or "rejected")] = (
                reasons.get(str(r.get("reason") or "rejected"), 0) + 1
            )
        else:
            cs = (r.get("case") or {}).get("status")
            if cs == "accepted":
                accepted += 1
            elif cs == "quarantined":
                quarantined += 1
            else:
                # 理论上不会发生
                quarantined += 1

    return {
        "input": str(path),
        "total": total,
        "accepted": accepted,
        "quarantined": quarantined,
        "rejected": rejected,
        "duplicate_skipped": reasons.get("duplicate_skip", 0),
        "reasons": reasons,
    }
