"""P2-6 数据摄入网关：显式 ``target`` 路由到 Mem0 画像 / Hindsight / Asset Store。

与 ``POST /api/memory/ingest`` 的 ``kind`` 分流不同：本模块按路线图 **v1 target** 命名，
供统一 ``POST /ingest`` 或工具化调用。
"""

from __future__ import annotations

import os
from typing import Any, Literal

from agent_os.config import Settings
from agent_os.knowledge.asset_ingest import IngestOptions, ingest_text
from agent_os.knowledge.asset_store import asset_store_from_settings
from agent_os.memory.controller import MemoryController
from agent_os.memory.models import MemoryLane, UserFact

IngestTargetV1 = Literal["mem0_profile", "hindsight", "asset_store"]

_VALID = frozenset({"mem0_profile", "hindsight", "asset_store"})


def _ingest_allow_llm() -> bool:
    return os.getenv("AGENT_OS_INGEST_ALLOW_LLM", "1").lower() not in ("0", "false", "no")


def run_ingest_v1(
    *,
    target: str,
    text: str,
    client_id: str,
    user_id: str | None,
    skill_id: str,
    settings: Settings,
    controller: MemoryController,
    mem_kind: str | None = None,
    task_id: str | None = None,
    source: str | None = "ingest_gateway",
    supersedes_event_id: str | None = None,
    weight_count: int | None = None,
) -> dict[str, Any]:
    """
    :param target: ``mem0_profile`` | ``hindsight`` | ``asset_store``
    :param mem_kind: 仅 ``mem0_profile``：``fact`` | ``preference``（默认 ``fact``）
    :param supersedes_event_id: 仅 ``hindsight``：可选，取代既有 ``event_id``（见 Hindsight JSONL）。
    :param weight_count: 仅 ``hindsight``：可选统计权重，默认 1，最大 10000。
    """
    t = target.strip().lower()
    if t not in _VALID:
        raise ValueError(f"未知 target={target!r}，须为 mem0_profile | hindsight | asset_store")

    raw = (text or "").strip()
    if not raw:
        raise ValueError("text 不能为空")

    cid = (client_id or "").strip() or "demo_client"
    sk = (skill_id or "").strip() or settings.default_skill_id

    if t == "mem0_profile":
        k = (mem_kind or "fact").strip().lower()
        if k == "fact":
            lane = MemoryLane.ATTRIBUTE
            fact_type: Any = "attribute"
            scope = "client_shared"
        elif k == "preference":
            lane = MemoryLane.ATTRIBUTE
            fact_type = "preference"
            scope = "client_shared" if user_id is None else "user_private"
        else:
            raise ValueError("mem0_profile 时 mem_kind 须为 fact | preference")
        fact = UserFact(
            lane=lane,
            client_id=cid,
            user_id=user_id,
            scope=scope,
            skill_id=sk,
            text=raw,
            fact_type=fact_type,
            source=source or "ingest_gateway",
        )
        r = controller.ingest_user_fact(fact)
        return {
            "status": "rejected" if r.policy_rejected else "ok",
            "target": t,
            "written_to": list(r.written_to),
            "dedup_skipped": r.dedup_skipped,
            "detail": r.dedup_reason,
            "policy_rejected": r.policy_rejected,
            "policy_reason": r.policy_reason,
        }

    if t == "hindsight":
        if controller.hindsight_store is None:
            raise ValueError("Hindsight 未启用（AGENT_OS_ENABLE_HINDSIGHT=0 或存储未初始化）")
        sid = (supersedes_event_id or "").strip() or None
        try:
            wc_raw = 1 if weight_count is None else int(weight_count)
        except (TypeError, ValueError):
            wc_raw = 1
        wc = max(1, min(wc_raw, 10000))
        fact = UserFact(
            lane=MemoryLane.TASK_FEEDBACK,
            client_id=cid,
            user_id=user_id,
            scope="task_scoped",
            skill_id=sk,
            text=raw,
            fact_type="feedback",
            task_id=task_id,
            source=source or "ingest_gateway",
            supersedes_event_id=sid,
            weight_count=wc,
        )
        r = controller.ingest_user_fact(fact)
        return {
            "status": "rejected" if r.policy_rejected else "ok",
            "target": t,
            "written_to": list(r.written_to),
            "dedup_skipped": r.dedup_skipped,
            "detail": r.dedup_reason,
            "policy_rejected": r.policy_rejected,
            "policy_reason": r.policy_reason,
        }

    # asset_store
    if not settings.enable_asset_store:
        raise ValueError("未启用 Asset Store（AGENT_OS_ENABLE_ASSET_STORE=0）")
    store = asset_store_from_settings(enable=True, path=settings.asset_store_path)
    opt = IngestOptions(
        client_id=cid,
        user_id=user_id,
        skill_id=sk,
        source=source,
        compliance_dir=settings.skill_compliance_dir,
        allow_llm=_ingest_allow_llm(),
    )
    r = ingest_text(raw, store=store, opt=opt)
    return {"status": r.get("status", "ok"), "target": t, "result": r}
