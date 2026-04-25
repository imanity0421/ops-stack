from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


AssetStatus = Literal["accepted", "quarantined", "rejected"]


def normalize_raw_for_hash(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def compute_content_hash(raw: str) -> str:
    """正文级强指纹：规范化空白后 SHA-256；用于同文去重，不用于防跨租户串库。"""
    return hashlib.sha256(normalize_raw_for_hash(raw).encode("utf-8")).hexdigest()


def compute_dedup_key(client_id: str, skill_id: str, user_id: str | None, content_hash: str) -> str:
    """租户 + skill + 用户作用域 + 正文指纹，唯一键（64 hex）。"""
    u = user_id or ""
    s = f"{client_id}\x00{skill_id}\x00{u}\x00{content_hash}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class AssetCase(BaseModel):
    """
    案例库记录：整存整取（raw_content 不切片）。
    embedding 不对 raw_content，而是对 retrieval_text（摘要+风格+标签）向量化。
    """

    case_id: str = Field(..., min_length=6)
    client_id: str = Field(..., min_length=1)
    user_id: str | None = None
    skill_id: str = Field(..., min_length=1)
    source: str | None = None

    raw_content: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    style_fingerprint: str = Field(..., min_length=1)
    key_excerpts: list[str] = Field(default_factory=list)

    tags: list[str] = Field(default_factory=list)
    platform: str | None = None
    content_type: str | None = None
    duration_bucket: str | None = None

    retrieval_text: str = Field(..., min_length=1, description="用于 embedding 的合成文本")

    #: 正文规范化后的 SHA-256（便于排查与对账）
    content_hash: str = Field(..., min_length=32, max_length=64)
    #: 租户+skill+用户+content_hash 的合成键，用于强去重查询
    dedup_key: str = Field(..., min_length=32, max_length=64)

    status: AssetStatus = "accepted"
    quality_score: float | None = None
    risk_flags: list[str] = Field(default_factory=list)
    reject_reason: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AssetSearchHit(BaseModel):
    case_id: str
    score: float | None = None
    summary: str
    style_fingerprint: str
    key_excerpts: list[str] = Field(default_factory=list)
    raw_content: str | None = None
    tags: list[str] = Field(default_factory=list)
    platform: str | None = None
    content_type: str | None = None
    duration_bucket: str | None = None
    created_at: str | None = None


class AssetStore(Protocol):
    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        limit: int = 4,
        include_raw: bool = False,
    ) -> list[AssetSearchHit]: ...

    def upsert_many(self, cases: Sequence[AssetCase]) -> dict[str, Any]: ...

    def find_case_id_by_dedup_key(self, dedup_key: str) -> str | None: ...

    def find_near_duplicate_case_id(
        self,
        retrieval_text: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        l2_max: float,
    ) -> str | None: ...

    def delete_by_case_id(self, case_id: str) -> dict[str, Any]: ...

    def delete_by_client_skill(self, client_id: str, skill_id: str) -> dict[str, Any]: ...


def _safe_short(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1] + "…"


def _lance_str_literal(s: str) -> str:
    """Lance/字符串过滤中单引号需写成 ''。"""
    return str(s).replace("'", "''")


def _row_in_scope(
    row: dict[str, Any],
    *,
    client_id: str,
    user_id: str | None,
    skill_id: str,
    only_accepted: bool = True,
) -> bool:
    if row.get("client_id") != client_id or row.get("skill_id") != skill_id:
        return False
    if only_accepted and row.get("status") != "accepted":
        return False
    ru = row.get("user_id")
    if user_id is not None:
        return ru == user_id
    # 未带 user 时：只检索「租户共享」案例（user_id 空），避免同租户多终端用户间串案
    return ru is None or ru == ""


def _row_to_hit(row: dict[str, Any], *, include_raw: bool) -> AssetSearchHit | None:
    try:
        key_excerpts = json.loads(row.get("key_excerpts") or "[]")
    except Exception:
        key_excerpts = []
    try:
        tags = json.loads(row.get("tags") or "[]")
    except Exception:
        tags = []
    case_id = str(row.get("case_id") or "")
    summary = str(row.get("summary") or "")
    if not case_id or not summary:
        return None
    return AssetSearchHit(
        case_id=case_id,
        score=float(row.get("_distance")) if row.get("_distance") is not None else None,
        summary=summary,
        style_fingerprint=str(row.get("style_fingerprint") or ""),
        key_excerpts=list(key_excerpts) if isinstance(key_excerpts, list) else [],
        raw_content=str(row.get("raw_content") or "") if include_raw else None,
        tags=list(tags) if isinstance(tags, list) else [],
        platform=row.get("platform"),
        content_type=row.get("content_type"),
        duration_bucket=row.get("duration_bucket"),
        created_at=str(row.get("created_at") or "") or None,
    )


def format_hits_for_agent(
    hits: Sequence[AssetSearchHit],
    *,
    include_raw: bool,
    temporal_grounding: bool = True,
) -> str:
    if not hits:
        return "（无）"
    blocks: list[str] = []
    for i, h in enumerate(hits, start=1):
        lines = [
            f"### Case {i} | id={h.case_id}"
            + (f" | score={h.score:.4f}" if h.score is not None else ""),
            f"- 摘要：{_safe_short(h.summary, 400)}",
            f"- 风格指纹：{_safe_short(h.style_fingerprint, 500)}",
            f"- 标签：{', '.join(h.tags) if h.tags else '（无）'}",
            f"- 场景/类型/长度：{h.platform or '（无）'} / {h.content_type or '（无）'} / {h.duration_bucket or '（无）'}",
            "- 关键片段：\n  - "
            + (
                "\n  - ".join(_safe_short(x, 220) for x in (h.key_excerpts or [])[:4])
                if h.key_excerpts
                else "（无）"
            ),
        ]
        if temporal_grounding:
            lines.insert(1, f"- 记录于：{h.created_at or '记录时间未知'}")
        blocks.append("\n".join(lines))
        if include_raw and h.raw_content:
            blocks.append("#### 原文（节选）\n" + _safe_short(h.raw_content, 2400))
    return "\n\n".join(blocks)


class NullAssetStore:
    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        limit: int = 4,
        include_raw: bool = False,
    ) -> list[AssetSearchHit]:
        _ = (query, client_id, user_id, skill_id, limit, include_raw)
        return []

    def upsert_many(self, cases: Sequence[AssetCase]) -> dict[str, Any]:
        return {"status": "skipped", "reason": "asset_store_disabled", "count": len(list(cases))}

    def find_case_id_by_dedup_key(self, dedup_key: str) -> str | None:
        _ = dedup_key
        return None

    def find_near_duplicate_case_id(
        self,
        retrieval_text: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        l2_max: float,
    ) -> str | None:
        _ = (retrieval_text, client_id, user_id, skill_id, l2_max)
        return None

    def delete_by_case_id(self, case_id: str) -> dict[str, Any]:
        _ = case_id
        return {"status": "skipped"}

    def delete_by_client_skill(self, client_id: str, skill_id: str) -> dict[str, Any]:
        _ = (client_id, skill_id)
        return {"status": "skipped"}


@dataclass(frozen=True)
class OpenAIEmbeddingConfig:
    model: str = "text-embedding-3-small"


def _embed_text_openai(text: str, *, cfg: OpenAIEmbeddingConfig) -> list[float]:
    """
    运行时与入库均可用的最小 embedding 实现。
    注意：运行时仅用于 query embedding，不做任何清洗/特征提取。
    """

    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE") or None,
    )
    r = client.embeddings.create(model=cfg.model, input=text[:8000])
    return list(r.data[0].embedding)


class LanceDbAssetStore:
    """
    LanceDB 封装：所有 DB 操作收敛在本模块中，避免在 factory/工具层直接写查询。
    """

    def __init__(
        self,
        *,
        path: Path,
        table_name: str = "asset_cases",
        embedding: OpenAIEmbeddingConfig | None = None,
    ) -> None:
        self._path = path
        self._table_name = table_name
        self._embedding = embedding or OpenAIEmbeddingConfig()
        self._db = None
        self._table = None

    def _connect(self) -> None:
        if self._db is not None:
            return
        try:
            import lancedb  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("未安装 lancedb；请安装 agent-os-runtime 的 asset-store 依赖") from e
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._path))

    def _open_table(self) -> bool:
        """若表已存在则打开并置 self._table；否则返回 False（LanceDB 0.30+ 禁止无 schema 的空表 create）。"""
        if self._table is not None:
            return True
        self._connect()
        try:
            self._table = self._db.open_table(self._table_name)  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    def upsert_many(self, cases: Sequence[AssetCase]) -> dict[str, Any]:
        self._connect()
        rows: list[dict[str, Any]] = []
        for c in cases:
            rows.append(
                {
                    "case_id": c.case_id,
                    "client_id": c.client_id,
                    "user_id": c.user_id,
                    "skill_id": c.skill_id,
                    "source": c.source,
                    "raw_content": c.raw_content,
                    "summary": c.summary,
                    "style_fingerprint": c.style_fingerprint,
                    "key_excerpts": json.dumps(c.key_excerpts, ensure_ascii=False),
                    "tags": json.dumps(c.tags, ensure_ascii=False),
                    "platform": c.platform,
                    "content_type": c.content_type,
                    "duration_bucket": c.duration_bucket,
                    "retrieval_text": c.retrieval_text,
                    "content_hash": c.content_hash,
                    "dedup_key": c.dedup_key,
                    "status": c.status,
                    "quality_score": c.quality_score,
                    "risk_flags": json.dumps(c.risk_flags, ensure_ascii=False),
                    "reject_reason": c.reject_reason,
                    "created_at": c.created_at,
                    "vector": _embed_text_openai(c.retrieval_text, cfg=self._embedding),
                }
            )
        if not rows:
            return {"status": "ok", "count": 0, "path": str(self._path), "table": self._table_name}
        # 首批写入时 create_table(data=rows) 推导语义；LanceDB 0.30+ 不允许 data=[] 无 schema
        if not self._open_table():
            self._table = self._db.create_table(  # type: ignore[union-attr]
                self._table_name, data=rows
            )
        else:
            self._table.add(rows)  # type: ignore[union-attr]
        return {
            "status": "ok",
            "count": len(rows),
            "path": str(self._path),
            "table": self._table_name,
        }

    def find_case_id_by_dedup_key(self, dedup_key: str) -> str | None:
        if not self._open_table():
            return None
        try:
            t = self._table.to_arrow()  # type: ignore[union-attr]
        except Exception as e:  # pragma: no cover
            logger.warning("find_case_id_by_dedup_key: to_arrow failed: %s", e)
            return None
        names = t.column_names
        if "dedup_key" not in names or "case_id" not in names:
            return None
        dct = t.to_pydict()
        keys = dct.get("dedup_key") or []
        cids = dct.get("case_id") or []
        for i, k in enumerate(keys):
            if k == dedup_key and i < len(cids) and cids[i] is not None:
                return str(cids[i])
        return None

    def find_near_duplicate_case_id(
        self,
        retrieval_text: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        l2_max: float,
    ) -> str | None:
        """对 retrieval_text 做向量近邻，在租户作用域内若 L2 距离小于阈值则视为近似重复。"""
        if not self._open_table():
            return None
        qv = _embed_text_openai(retrieval_text, cfg=self._embedding)
        over = 40
        raw_list = self._table.search(qv, vector_column_name="vector").limit(over).to_list()  # type: ignore[union-attr]
        for row in raw_list:
            if not isinstance(row, dict):
                continue
            if not _row_in_scope(
                row, client_id=client_id, user_id=user_id, skill_id=skill_id, only_accepted=True
            ):
                continue
            d = row.get("_distance")
            if d is None:
                continue
            try:
                if float(d) < float(l2_max):
                    cid = str(row.get("case_id") or "")
                    return cid or None
            except (TypeError, ValueError):
                continue
        return None

    def delete_by_case_id(self, case_id: str) -> dict[str, Any]:
        if not self._open_table():
            return {"status": "ok", "case_id": case_id, "note": "no_table"}
        c = _lance_str_literal(case_id)
        try:
            self._table.delete(f"case_id == '{c}'")  # type: ignore[union-attr]
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "case_id": case_id}

    def delete_by_client_skill(self, client_id: str, skill_id: str) -> dict[str, Any]:
        """删除某租户+skill 下全部案例行（清库/回退用，慎用）。"""
        if not self._open_table():
            return {
                "status": "ok",
                "client_id": client_id,
                "skill_id": skill_id,
                "note": "no_table",
            }
        c = _lance_str_literal(client_id)
        s = _lance_str_literal(skill_id)
        try:
            self._table.delete(f"client_id == '{c}' AND skill_id == '{s}'")  # type: ignore[union-attr]
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "client_id": client_id, "skill_id": skill_id}

    def search(
        self,
        query: str,
        *,
        client_id: str,
        user_id: str | None,
        skill_id: str,
        limit: int = 4,
        include_raw: bool = False,
    ) -> list[AssetSearchHit]:
        if not self._open_table():
            return []
        qv = _embed_text_openai(query, cfg=self._embedding)
        # 多取后仅在内存中按租户/用户过滤，避免无过滤回退导致串租户；不依赖 .where 方言
        over = max(limit * 30, 50)
        raw_list: list[dict[str, Any]] = []
        try:
            raw_list = self._table.search(qv, vector_column_name="vector").limit(over).to_list()  # type: ignore[union-attr]
        except Exception as e:  # pragma: no cover
            logger.error("AssetStore vector search failed: %s", e)
            return []

        hits: list[AssetSearchHit] = []
        for row in raw_list:
            if not isinstance(row, dict):
                continue
            if not _row_in_scope(
                row, client_id=client_id, user_id=user_id, skill_id=skill_id, only_accepted=True
            ):
                continue
            hit = _row_to_hit(row, include_raw=include_raw)
            if hit is not None:
                hits.append(hit)
            if len(hits) >= limit:
                break
        return hits


def asset_store_from_settings(*, enable: bool, path: Path) -> AssetStore:
    if not enable:
        return NullAssetStore()
    return LanceDbAssetStore(path=path)
