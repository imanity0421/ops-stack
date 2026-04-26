from __future__ import annotations

import logging
import os
from typing import Sequence

logger = logging.getLogger(__name__)


def _candidate_block(candidates: Sequence[str], *, max_candidates: int) -> str:
    lines: list[str] = []
    for i, c in enumerate(candidates[:max_candidates], start=1):
        t = str(c).strip()
        if not t:
            continue
        lines.append(f"{i}. {t[:1800]}")
    return "\n".join(lines)


def synthesize_hindsight_context(
    *,
    query: str,
    candidates: Sequence[str],
    model: str | None = None,
    max_candidates: int = 20,
) -> str:
    """
    对 Hindsight 候选池做可选 LLM 加工：相关性筛选、语义去重、冲突合并和摘要。

    调用方必须先完成租户过滤、metadata 加权与 top-N 剪枝；本函数不负责全库检索。
    失败时返回确定性候选拼接，保证记忆检索链路不中断。
    """

    clean = [str(x).strip() for x in candidates if str(x).strip()]
    if not clean:
        return ""
    block = _candidate_block(clean, max_candidates=max_candidates)
    if not block:
        return ""

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_API_BASE") or None,
        )
        mid = model or os.getenv("AGENT_OS_MODEL", "gpt-4o-mini")
        prompt = (
            "你是 Agent 记忆检索的 Hindsight 加工层。下面是已经过租户过滤、相似度与 metadata "
            "加权排序后的历史反馈/教训候选。请只基于候选内容，输出本轮最可用的经验摘要。\n\n"
            "要求：\n"
            "1. 去掉与当前问题无关、重复、一次性噪声的候选。\n"
            "2. 合并语义重复经验，保留来源范围提示（如同用户/同客户/同任务，若候选文本里可见）。\n"
            "3. 若候选冲突，明确说明冲突并给出采用条件。\n"
            "4. 输出 3-6 条，中文短句，每条必须可执行。\n"
            "5. 不要编造候选中没有的信息。\n\n"
            f"当前问题：{query[:1200]}\n\n候选：\n{block}"
        )
        r = client.chat.completions.create(
            model=mid,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        text = (r.choices[0].message.content or "").strip()
        if text:
            return "【Hindsight 加工摘要】\n" + text
    except Exception as e:
        logger.warning("Hindsight synthesis failed, fallback to ranked candidates: %s", e)

    return "\n---\n".join(clean[:max_candidates])
