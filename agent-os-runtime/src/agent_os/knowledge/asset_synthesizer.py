from __future__ import annotations

import logging
import os
from typing import Sequence

from agent_os.knowledge.asset_store import AssetSearchHit, format_hits_for_agent

logger = logging.getLogger(__name__)


def synthesize_asset_context(
    *,
    query: str,
    hits: Sequence[AssetSearchHit],
    include_raw: bool,
    asset_type: str | None = None,
    model: str | None = None,
    max_candidates: int = 12,
) -> str:
    """
    对 Asset Store 候选做可选 LLM 加工，输出更适合注入 prompt 的资产上下文。

    调用方必须先完成权限过滤、scope fallback、asset_type/skill hint 排序和 top-N 剪枝。
    失败时回退到确定性格式化，保证检索链路不中断。
    """

    clean = [h for h in hits[:max_candidates] if h.summary.strip()]
    if not clean:
        return ""

    deterministic = format_hits_for_agent(
        clean,
        include_raw=include_raw,
        temporal_grounding=True,
    )
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_API_BASE") or None,
        )
        mid = model or os.getenv("AGENT_OS_MODEL", "gpt-4o-mini")
        at = asset_type or "mixed"
        prompt = (
            "你是 Agent 资产检索的加工层。下面是已经过权限过滤、scope fallback、向量检索与排序后的资产候选。"
            "请只基于候选内容，输出本轮可用的资产上下文。\n\n"
            "资产类型说明：\n"
            "- style_reference：风格/Few-shot 范例，只可参考语气、结构、节奏、排版，不要照抄事实。\n"
            "- source_material：背景素材，可提取事实、经历、故事细节，不要模仿格式。\n\n"
            "输出要求：\n"
            "1. 若候选含 style_reference，输出 [Few-Shot Guidance]，总结可模仿的结构、节奏和表达要点。\n"
            "2. 若候选含 source_material，输出 [Background Context]，提炼可使用的事实、故事、实体与细节。\n"
            "3. 去掉重复、低相关或明显不适合当前问题的候选。\n"
            "4. 不要编造候选中没有的信息；不要泄露未要求的原文长段。\n"
            "5. 中文输出，短而可执行。\n\n"
            f"当前问题：{query[:1200]}\n"
            f"请求资产类型：{at}\n\n"
            f"候选资产：\n{deterministic[:12000]}"
        )
        r = client.chat.completions.create(
            model=mid,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        text = (r.choices[0].message.content or "").strip()
        if text:
            return "【Asset Store 加工摘要】\n" + text
    except Exception as e:
        logger.warning("Asset synthesis failed, fallback to formatted hits: %s", e)

    return deterministic
