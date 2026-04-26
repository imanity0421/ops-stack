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
    AssetScope,
    AssetStatus,
    AssetStore,
    AssetType,
    SYSTEM_GLOBAL_CLIENT_ID,
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
    scope: AssetScope | None = None
    asset_type: AssetType | None = None
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


def _resolve_scope(opt: IngestOptions) -> AssetScope:
    if opt.scope is not None:
        return opt.scope
    if opt.client_id == SYSTEM_GLOBAL_CLIENT_ID:
        return "system"
    if opt.user_id:
        return "user_private"
    return "client_shared"


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
        "你是资产库的入库裁判。判断下面文本是否具有可复用业务价值："
        "可以是可模仿结构/语气的成品文案，也可以是可提取事实细节的背景素材。输出严格 JSON："
        '{"status":"accepted|quarantined|rejected","reason":"...简短原因..."}\n'
        "判定要点：是否明显乱码/广告垃圾/敏感风险、是否过短、是否完全缺少上下文或事实价值。\n\n"
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
            "asset_type": opt.asset_type or "style_reference",
            "primary_skill_hint": opt.skill_id,
            "applicable_skill_ids": [opt.skill_id],
            "skill_confidence": 0.5,
            "feature_summary": raw[:160],
            "summary": raw[:400],
            "style_fingerprint": "（未抽取：allow_llm=0）",
            "tags": [],
            "style_tags": [],
            "content_tags": [],
            "platform": None,
            "content_type": None,
            "duration_bucket": None,
            "key_excerpts": [],
        }

    prompt = (
        "你是商业运营资产分类与特征提取器。对下面任意文本判断资产类型，并抽取用于检索的特征。输出严格 JSON，字段：\n"
        "{\n"
        '  "asset_type": "style_reference|source_material",\n'
        '  "primary_skill_hint": "推断适用 skill；背景素材用 global",\n'
        '  "applicable_skill_ids": ["可适用的 skill id；背景素材至少含 global"],\n'
        '  "skill_confidence": 0.0,\n'
        '  "feature_summary": "50字以内的核心检索特征",\n'
        '  "summary": "高密度摘要（1段）",\n'
        '  "style_fingerprint": "风格指纹：语气/节奏/结构/表达方式/句式偏好等（1段）",\n'
        '  "tags": ["若干短标签"],\n'
        '  "style_tags": ["3-5个排版与语气特征；背景素材可为空"],\n'
        '  "content_tags": ["3-5个实体与主题特征；风格范例也可有"],\n'
        '  "platform": "交付场景或使用环境，如 chat|doc|web|api|report|code|other|unknown",\n'
        '  "content_type": "内容类型，如 方案|说明|复盘|清单|报告|代码|其他|unknown",\n'
        '  "duration_bucket": "长度区间，如 short|medium|long|unknown",\n'
        '  "key_excerpts": ["关键片段1","关键片段2","关键片段3"]\n'
        "}\n"
        "规则：成品文案/剧本/营销软文归 style_reference；个人经历/采访/产品资料/事实背景归 source_material。"
        "source_material 不用于模仿语气，style_fingerprint 可写“背景素材，不用于模仿”。不要输出多余字段。\n\n"
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

    comp = check_skill_compliance(raw, opt.skill_id, opt.compliance_dir)
    if comp:
        return {"status": "rejected", "reason": "compliance", "violations": comp}

    try:
        status, reason = _llm_gatekeeper(raw, opt=opt)
    except Exception as e:
        logger.warning("gatekeeper failed: %s", e)
        status = "quarantined"
        reason = "gatekeeper_failed"
    if status == "rejected":
        return {"status": "rejected", "reason": reason}

    try:
        feat = _llm_extract_features(raw, opt=opt)
        asset_type = (
            feat.get("asset_type")
            if feat.get("asset_type") in ("style_reference", "source_material")
            else opt.asset_type or "style_reference"
        )
        primary_skill_hint = str(feat.get("primary_skill_hint") or opt.skill_id or "global").strip()
        applicable_skill_ids = feat.get("applicable_skill_ids") or []
        skill_confidence = feat.get("skill_confidence")
        feature_summary = str(feat.get("feature_summary") or "").strip()
        summary = str(feat.get("summary") or "").strip()
        style = str(feat.get("style_fingerprint") or "").strip()
        tags = feat.get("tags") or []
        style_tags = feat.get("style_tags") or []
        content_tags = feat.get("content_tags") or []
        key_excerpts = feat.get("key_excerpts") or []
        platform = feat.get("platform")
        content_type = feat.get("content_type")
        duration_bucket = feat.get("duration_bucket")
    except Exception as e:
        logger.warning("feature extract failed: %s", e)
        status = "quarantined"
        reason = reason or "feature_extract_failed"
        asset_type = opt.asset_type or "style_reference"
        primary_skill_hint = opt.skill_id
        applicable_skill_ids = [opt.skill_id]
        skill_confidence = None
        feature_summary = raw[:160].strip()
        summary = raw[:400].strip()
        style = "（特征抽取失败，需人工复核）"
        tags = []
        style_tags = []
        content_tags = []
        key_excerpts = []
        platform = None
        content_type = None
        duration_bucket = None

    if not summary or not style or not feature_summary:
        status = "quarantined"
        reason = reason or "missing_summary_or_style_or_feature"

    scope = _resolve_scope(opt)
    owner_user_id = opt.user_id if scope == "user_private" else None
    ch = compute_content_hash(raw)
    dk = compute_dedup_key(
        opt.client_id,
        primary_skill_hint,
        owner_user_id,
        ch,
        scope=scope,
        asset_type=asset_type,
    )
    existing = store.find_case_id_by_dedup_key(dk)
    if existing:
        return {"status": "duplicate_skip", "reason": "exact_dedup", "case_id": existing}

    retrieval_text = "\n".join(
        [
            f"资产类型：{asset_type}",
            f"检索特征：{feature_summary}",
            f"摘要：{summary}",
            f"风格：{style}",
            f"标签：{', '.join(tags) if isinstance(tags, list) else ''}",
            f"风格标签：{', '.join(style_tags) if isinstance(style_tags, list) else ''}",
            f"内容标签：{', '.join(content_tags) if isinstance(content_tags, list) else ''}",
            f"适用技能：{', '.join(applicable_skill_ids) if isinstance(applicable_skill_ids, list) else primary_skill_hint}",
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
                asset_type=asset_type,
            )
            if near_id:
                return {"status": "duplicate_skip", "reason": "near_dedup", "case_id": near_id}
        except (TypeError, ValueError) as e:
            logger.debug("near dedup skipped: %s", e)

    case = AssetCase(
        case_id=str(uuid4()),
        scope=scope,
        asset_type=asset_type,
        client_id=opt.client_id,
        user_id=owner_user_id,
        owner_user_id=owner_user_id,
        skill_id=primary_skill_hint,
        primary_skill_hint=primary_skill_hint,
        applicable_skill_ids=list(applicable_skill_ids)
        if isinstance(applicable_skill_ids, list)
        else [primary_skill_hint],
        skill_confidence=float(skill_confidence)
        if isinstance(skill_confidence, int | float)
        else None,
        source=opt.source,
        raw_content=raw.strip(),
        summary=summary,
        feature_summary=feature_summary,
        style_fingerprint=style,
        key_excerpts=list(key_excerpts) if isinstance(key_excerpts, list) else [],
        tags=list(tags) if isinstance(tags, list) else [],
        style_tags=list(style_tags) if isinstance(style_tags, list) else [],
        content_tags=list(content_tags) if isinstance(content_tags, list) else [],
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
    try:
        raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        return {
            "input": str(path),
            "status": "error",
            "reason": "invalid_input_file",
            "error": str(e),
            "total": 0,
            "accepted": 0,
            "quarantined": 0,
            "rejected": 0,
            "duplicate_skipped": 0,
            "reasons": {"invalid_input_file": 1},
        }
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
