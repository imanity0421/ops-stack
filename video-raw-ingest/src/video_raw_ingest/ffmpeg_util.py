"""FFmpeg / ffprobe 封装：抽取 16kHz mono WAV、读取时长与帧率。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def which_binary(name: str) -> str | None:
    override = os.environ.get(f"{name.upper()}_BIN") or os.environ.get(name.upper())
    if override and Path(override).is_file():
        return str(Path(override).resolve())
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32" and name in ("ffmpeg", "ffprobe"):
        exe = f"{name}.exe"
        for base in (
            Path(r"C:\ffmpeg\bin"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
        ):
            p = base / exe
            if p.is_file():
                return str(p.resolve())
    return None


def run_ffprobe(video: Path) -> dict[str, Any]:
    ffprobe = which_binary("ffprobe")
    if not ffprobe:
        raise FileNotFoundError(
            "未找到 ffprobe。请安装 FFmpeg 并加入 PATH，或设置 FFPROBE_BIN。"
        )
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video),
    ]
    out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
    return json.loads(out)


def video_stream_fps(probe: dict[str, Any]) -> float:
    """从 ffprobe JSON 取视频流帧率（用于时间戳近似；VFR 仅为近似）。"""
    for s in probe.get("streams") or []:
        if s.get("codec_type") != "video":
            continue
        avg = s.get("avg_frame_rate") or ""
        r = s.get("r_frame_rate") or ""
        for frac in (avg, r):
            if isinstance(frac, str) and frac and "/" in frac:
                a, b = frac.split("/", 1)
                try:
                    fa, fb = float(a), float(b)
                    if fb > 0 and fa > 0:
                        return fa / fb
                except (TypeError, ValueError):
                    pass
    return 25.0


def format_duration_sec(probe: dict[str, Any]) -> float | None:
    fmt = probe.get("format") or {}
    try:
        if "duration" in fmt:
            return float(fmt["duration"])
    except (TypeError, ValueError):
        pass
    return None


def extract_wav_16k_mono(video: Path, wav_out: Path, ffmpeg: str) -> None:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_out),
    ]
    r = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        sys.stderr.write(err)
        if not err.endswith("\n"):
            sys.stderr.write("\n")
        raise subprocess.CalledProcessError(r.returncode, cmd, stderr=err)
