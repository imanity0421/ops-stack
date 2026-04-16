"""OpenAI 兼容 Chat Completions 薄封装。"""

from __future__ import annotations

from typing import Any


def chat_complete(
    *,
    api_key: str,
    base_url: str,
    model: str,
    user_prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    r = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (r.choices[0].message.content or "").strip()
