"""MinerU：子进程调用，逐帧解析为 Markdown。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def resolve_mineru_command() -> list[str]:
    """
    返回 argv 前缀（不含 -p/-o）。
    优先 MINERU_BIN；否则 shutil.which('mineru')；
    再否则 MINERU_PYTHON -m mineru（若存在）。
    """
    bin_override = os.environ.get("MINERU_BIN", "").strip()
    if bin_override:
        return [bin_override]

    w = shutil.which("mineru")
    if w:
        return [w]

    py = os.environ.get("MINERU_PYTHON", "").strip()
    if py and Path(py).is_file():
        return [py, "-m", "mineru"]

    return [sys.executable, "-m", "mineru"]


def _collect_markdown(mineru_out: Path) -> str:
    md_files = sorted(mineru_out.rglob("*.md"))
    if not md_files:
        return ""
    # 取最长文本（常见为主文档）
    best = ""
    for p in md_files:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(t) > len(best):
            best = t
    return best.strip()


def run_mineru_on_image(
    image_path: Path,
    mineru_out_dir: Path,
    *,
    backend: str | None = None,
    extra_args: list[str] | None = None,
) -> tuple[str, str | None]:
    """
    返回 (markdown_text, error_message_or_none)
    """
    mineru_out_dir.mkdir(parents=True, exist_ok=True)
    cmd = resolve_mineru_command() + [
        "-p",
        str(image_path.resolve()),
        "-o",
        str(mineru_out_dir.resolve()),
    ]
    if backend:
        cmd.extend(["-b", backend])
    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    r = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return "", err
    md = _collect_markdown(mineru_out_dir)
    return md, None


def run_mineru_all_keyframes(
    out_root: Path,
    keyframes: list[dict[str, Any]],
    *,
    backend: str | None = None,
    extra_args: list[str] | None = None,
    fail_fast: bool = False,
) -> list[dict[str, Any]]:
    """
    keyframes: list of dicts with frame_relpath relative to out_root
    返回 slides 列表，每项含 mineru_markdown / mineru_error
    """
    slides: list[dict[str, Any]] = []
    for kf in keyframes:
        rel = kf.get("frame_relpath") or ""
        frame_path = out_root / rel
        idx = int(kf.get("index", len(slides)))
        mineru_dir = out_root / "slides" / "mineru" / f"{idx:04d}"
        if mineru_dir.exists():
            shutil.rmtree(mineru_dir, ignore_errors=True)
        mineru_dir.mkdir(parents=True, exist_ok=True)

        md, err = run_mineru_on_image(
            frame_path,
            mineru_dir,
            backend=backend,
            extra_args=extra_args,
        )
        slides.append(
            {
                "index": idx,
                "timestamp_sec": float(kf.get("timestamp_sec", 0.0)),
                "frame_relpath": rel,
                "mineru_output_dir": str(mineru_dir.relative_to(out_root)).replace(
                    "\\", "/"
                ),
                "mineru_markdown": md,
                "mineru_error": err,
            }
        )
        if err and fail_fast:
            break
    return slides
