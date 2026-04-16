"""高层插件：连接检测、基于 lesson_merged 的摘要与质量提示。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import chat_complete
from .env_loader import get_openai_settings


def ping(model_override: str | None = None) -> tuple[bool, str]:
    """返回 (ok, message)。"""
    api_key, base_url, model = get_openai_settings(model_override)
    if not api_key:
        return False, "未配置 OPENAI_API_KEY"
    try:
        text = chat_complete(
            api_key=api_key,
            base_url=base_url,
            model=model,
            user_prompt="只回复两个汉字：好的",
            system_prompt=None,
            max_tokens=32,
            temperature=0,
        )
        return True, f"base_url={base_url!r} model={model!r} 回复={text!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def load_lesson_merged(lesson_dir: Path) -> dict[str, Any]:
    p = lesson_dir / "lesson_merged.json"
    if not p.is_file():
        raise FileNotFoundError(f"未找到 lesson_merged.json: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def summarize_lesson(
    lesson_dir: Path,
    *,
    model_override: str | None = None,
) -> str:
    """根据合并结果生成短摘要（供人工/下游快速了解）。"""
    data = load_lesson_merged(lesson_dir)
    api_key, base_url, model = get_openai_settings(model_override)
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")

    speech = data.get("speech") or {}
    segs = speech.get("segments") or []
    speech_text = "\n".join(
        f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '')}"
        for s in segs[:80]
    )
    if len(segs) > 80:
        speech_text += f"\n…（共 {len(segs)} 段，已截断）"

    slides = (data.get("visual") or {}).get("slides") or []
    vis_parts: list[str] = []
    for s in slides[:40]:
        ts = s.get("timestamp_sec", 0)
        md = (s.get("mineru_markdown") or "").strip()
        err = s.get("mineru_error")
        if err:
            vis_parts.append(f"[{ts}s] 错误: {err}")
        elif md:
            vis_parts.append(f"[{ts}s] {md[:500]}")
    visual_blob = "\n\n".join(vis_parts)
    if len(slides) > 40:
        visual_blob += f"\n…（共 {len(slides)} 帧，已截断）"

    system = (
        "你是课程编辑助手。根据给定的口播时间轴与画面 OCR/Markdown，"
        "用中文写一段结构化短摘要：课程主题、知识要点列表、可能的缺口（如画面缺失）。"
        "不要编造视频中不存在的内容。"
    )
    user = f"【口播片段】\n{speech_text}\n\n【画面文字】\n{visual_blob}"
    return chat_complete(
        api_key=api_key,
        base_url=base_url,
        model=model,
        user_prompt=user,
        system_prompt=system,
        max_tokens=2048,
        temperature=0.3,
    )


def suggest_issues(
    lesson_dir: Path,
    *,
    model_override: str | None = None,
) -> str:
    """列出可能的数据质量问题（漏转写、空画面等），供抽检。"""
    data = load_lesson_merged(lesson_dir)
    api_key, base_url, model = get_openai_settings(model_override)
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")

    compact = json.dumps(data, ensure_ascii=False)[:120_000]
    system = (
        "你是质检助手。输入为一节课的结构化合并 JSON（口播+画面）。"
        "请用中文列出可能的问题：例如口播为空、画面全失败、时间线异常等；"
        "若无法判断则写「未发现明显结构问题」。不要编造。"
    )
    user = f"lesson_merged 数据（可能截断）：\n{compact}"
    return chat_complete(
        api_key=api_key,
        base_url=base_url,
        model=model,
        user_prompt=user,
        system_prompt=system,
        max_tokens=2048,
        temperature=0.2,
    )
