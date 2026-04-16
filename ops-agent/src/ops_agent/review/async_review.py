from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from openai import OpenAI

from ops_agent.memory.hindsight_store import HindsightStore

logger = logging.getLogger(__name__)


def transcript_to_text(transcript: list[tuple[str, str]]) -> str:
    """将 (role, content) 列表转为可读对话文本。"""
    lines: list[str] = []
    for role, content in transcript:
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_lessons(transcript_text: str, model: str | None) -> str:
    mid = model or os.getenv("OPS_AGENT_MODEL", "gpt-4o-mini")
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE") or None,
    )
    prompt = (
        "你是运营复盘助手。根据以下对话，总结 1～3 条可复用的「教训/注意事项」，"
        "每条一行，用中文，不要重复客户已写在 Mem0 的静态事实。"
        "若无值得记录的教训，输出单独一行：无。\n\n对话：\n"
        f"{transcript_text[:12000]}"
    )
    r = client.chat.completions.create(
        model=mid,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return (r.choices[0].message.content or "").strip()


class AsyncReviewService:
    """会话结束后异步写入 Hindsight（不阻塞 CLI 退出）。"""

    def __init__(
        self,
        store: HindsightStore,
        *,
        enabled: bool = True,
        model: str | None = None,
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._model = model

    @classmethod
    def from_env(cls, store: HindsightStore) -> AsyncReviewService:
        return cls(
            store,
            enabled=os.getenv("OPS_ASYNC_REVIEW_ON_EXIT", "1").lower() not in ("0", "false", "no"),
            model=os.getenv("OPS_ASYNC_REVIEW_MODEL"),
        )

    def submit(
        self,
        *,
        client_id: str,
        user_id: str | None,
        task_id: str | None,
        transcript: list[tuple[str, str]],
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> threading.Thread | None:
        if not self._enabled:
            logger.info("AsyncReview 已关闭 (OPS_ASYNC_REVIEW_ON_EXIT)")
            return None
        if not transcript:
            return None
        text_blob = transcript_to_text(transcript)
        if len(text_blob) < 20:
            return None

        def worker() -> None:
            try:
                raw = _extract_lessons(text_blob, self._model)
                if not raw or raw == "无" or raw.startswith("无。"):
                    logger.info("AsyncReview: 模型认为无需写入教训")
                    if on_done:
                        on_done({"status": "skipped"})
                    return
                for block in raw.split("\n"):
                    line = block.strip().lstrip("0123456789.-、)） ")
                    if len(line) < 8:
                        continue
                    self._store.append_lesson(
                        client_id=client_id,
                        text=line,
                        user_id=user_id,
                        task_id=task_id,
                        source="async_review",
                    )
                logger.info("AsyncReview: 已写入 Hindsight")
                if on_done:
                    on_done({"status": "ok"})
            except Exception as e:
                logger.exception("AsyncReview 失败: %s", e)
                if on_done:
                    on_done({"status": "error", "error": str(e)})

        t = threading.Thread(target=worker, name="ops-agent-async-review", daemon=False)
        t.start()
        return t

    def submit_and_wait(
        self,
        *,
        client_id: str,
        user_id: str | None,
        task_id: str | None,
        transcript: list[tuple[str, str]],
        join_timeout_sec: float = 120.0,
    ) -> None:
        """同步等待复盘线程结束（CLI 退出前调用，避免进程过早退出导致复盘中断）。"""
        t = self.submit(
            client_id=client_id,
            user_id=user_id,
            task_id=task_id,
            transcript=transcript,
        )
        if t is not None:
            t.join(timeout=join_timeout_sec)
