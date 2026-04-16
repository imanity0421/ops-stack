"""命令行入口：run（完整流水线）、llm（可选 OpenAI 兼容插件）。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .ffmpeg_util import (
    extract_wav_16k_mono,
    format_duration_sec,
    run_ffprobe,
    video_stream_fps,
    which_binary,
)
from .merge import build_merged, write_merged_json, write_merged_markdown
from .mineru_run import run_mineru_all_keyframes
from .output_layout import promote_staging_to_final, resolve_work_dir
from .paths import default_output_dir_for_video
from .slide_extract import extract_keyframes
from .validate import validate_merged, write_validation_report
from .whisperx_run import run_whisperx


def _probe_summary(probe: dict[str, Any]) -> dict[str, Any]:
    fmt = probe.get("format") or {}
    return {
        "format_name": fmt.get("format_name"),
        "duration_sec": format_duration_sec(probe),
        "size": fmt.get("size"),
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_pipeline(
    *,
    video: Path,
    out_dir: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行 run 的核心步骤，向 out_dir 写入。返回 (merged, report)。"""
    ffmpeg = which_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")

    probe = run_ffprobe(video)
    duration_sec = format_duration_sec(probe)
    fps = video_stream_fps(probe)
    summary = _probe_summary(probe)

    work = out_dir / "_work"
    wav = work / "audio_16k.wav"
    whisper_dir = out_dir / "whisperx"
    slides_dir = out_dir / "slides"
    frames_dir = slides_dir / "frames"

    if not args.skip_audio:
        print(f"[1/5] 抽取 WAV -> {wav}", flush=True)
        extract_wav_16k_mono(video, wav, ffmpeg)
    elif not wav.is_file():
        raise FileNotFoundError("缺少 _work/audio_16k.wav 且指定了 --skip-audio")

    if not args.skip_whisperx:
        print("[2/5] WhisperX 转写...", flush=True)
        lang = None if str(args.language).lower() == "auto" else args.language
        speech = run_whisperx(
            wav,
            whisper_dir,
            model_name=args.whisperx_model,
            device=args.device,
            batch_size=args.batch_size,
            language=lang,
        )
    else:
        seg_path = whisper_dir / "segments.json"
        if not seg_path.is_file():
            raise FileNotFoundError("缺少 whisperx/segments.json 且指定了 --skip-whisperx")
        speech = _load_json(seg_path)

    if not args.skip_slides:
        print("[3/5] 抽取关键帧...", flush=True)
        _records, _meta = extract_keyframes(
            video,
            frames_dir,
            similarity_threshold=args.similarity,
            max_frames=args.max_frames,
            fps_hint=fps,
            duration_sec=duration_sec,
            commit_mode=args.keyframe_commit,
            inter_frame_stability=args.keyframe_inter_stability,
            min_stable_seconds=args.keyframe_min_stable_seconds,
            max_transition_sec=args.keyframe_max_transition_sec,
        )
        kpath = slides_dir / "keyframes.json"
        keyframes_data = _load_json(kpath)
        keyframes_raw = keyframes_data.get("keyframes") or []
    else:
        kpath = slides_dir / "keyframes.json"
        if not kpath.is_file():
            raise FileNotFoundError("缺少 slides/keyframes.json 且指定了 --skip-slides")
        keyframes_data = _load_json(kpath)
        keyframes_raw = keyframes_data.get("keyframes") or []

    slides_json = slides_dir / "slides.json"
    if not args.skip_mineru:
        print("[4/5] MinerU 解析画面（可能较久）...", flush=True)
        backend = args.mineru_backend or (os.environ.get("MINERU_BACKEND") or None)
        extra = []
        if args.mineru_extra:
            extra = args.mineru_extra.split()
        slides = run_mineru_all_keyframes(
            out_dir,
            keyframes_raw,
            backend=backend,
            extra_args=extra or None,
            fail_fast=args.mineru_fail_fast,
        )
        slides_json.write_text(
            json.dumps(slides, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        if not slides_json.is_file():
            raise FileNotFoundError("缺少 slides/slides.json 且指定了 --skip-mineru")
        slides = json.loads(slides_json.read_text(encoding="utf-8"))

    print("[5/5] 结构合并与校验...", flush=True)
    merged = build_merged(
        video_path=video,
        duration_sec=duration_sec,
        probe_summary=summary,
        speech=speech,
        slides=slides,
    )
    if not args.skip_merge:
        write_merged_json(merged, out_dir / "lesson_merged.json")
        write_merged_markdown(merged, out_dir / "lesson_merged.md")

    report = validate_merged(
        merged,
        out_dir,
        require_speech=args.require_speech,
        require_visual_text=args.require_visual_text,
    )
    write_validation_report(report, out_dir / "validation_report.json")
    return merged, report


def cmd_run(args: argparse.Namespace) -> int:
    video = Path(args.video).resolve()
    if not video.is_file():
        print(f"找不到视频文件: {video}", file=sys.stderr)
        return 1

    final_out = (
        Path(args.out_dir).resolve()
        if args.out_dir
        else default_output_dir_for_video(video)
    )

    if args.replace and args.force_in_place:
        print("不能同时使用 --replace 与 --force-in-place", file=sys.stderr)
        return 1

    staging_path: Path | None = None
    work_out: Path

    try:
        work_out, staging_path = resolve_work_dir(
            final_out,
            replace=args.replace,
            force_in_place=args.force_in_place,
        )
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        merged, report = _run_pipeline(video=video, out_dir=work_out, args=args)
    except Exception as e:
        if staging_path is not None and staging_path.exists():
            shutil.rmtree(staging_path, ignore_errors=True)
        print(f"处理失败: {e}", file=sys.stderr)
        return 1

    if report.get("status") != "ok":
        if staging_path is not None and staging_path.exists():
            shutil.rmtree(staging_path, ignore_errors=True)
        print("校验失败:", file=sys.stderr)
        for err in report.get("errors") or []:
            print(f"  - {err}", file=sys.stderr)
        return 2

    for w in report.get("warnings") or []:
        print(f"警告: {w}", file=sys.stderr)

    if staging_path is not None:
        try:
            promote_staging_to_final(final_out, staging_path)
        except OSError as e:
            print(f"替换输出目录失败: {e}", file=sys.stderr)
            return 1

    print(f"完成。输出目录: {final_out}")
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    from .llm import plugin as llm_plugin
    from .llm.env_loader import load_env_files, resolve_repo_root

    repo = resolve_repo_root()
    load_env_files(repo, args.env_file)

    cmd = args.llm_cmd
    if cmd == "ping":
        ok, msg = llm_plugin.ping(args.model or None)
        print(msg, flush=True)
        return 0 if ok else 2

    if cmd == "summarize":
        lesson = Path(args.lesson_dir).resolve()
        if not lesson.is_dir():
            print(f"目录不存在: {lesson}", file=sys.stderr)
            return 1
        try:
            text = llm_plugin.summarize_lesson(lesson, model_override=args.model or None)
        except Exception as e:
            print(f"失败: {e}", file=sys.stderr)
            return 1
        out = (
            Path(args.output).resolve()
            if args.output
            else lesson / "llm_summary.md"
        )
        out.write_text(text, encoding="utf-8")
        print(f"已写入: {out}", flush=True)
        return 0

    if cmd == "suggest-issues":
        lesson = Path(args.lesson_dir).resolve()
        if not lesson.is_dir():
            print(f"目录不存在: {lesson}", file=sys.stderr)
            return 1
        try:
            text = llm_plugin.suggest_issues(lesson, model_override=args.model or None)
        except Exception as e:
            print(f"失败: {e}", file=sys.stderr)
            return 1
        out = (
            Path(args.output).resolve()
            if args.output
            else lesson / "llm_quality_hints.md"
        )
        out.write_text(text, encoding="utf-8")
        print(f"已写入: {out}", flush=True)
        return 0

    print(f"未知 llm 子命令: {cmd}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="video-raw-ingest",
        description="课程视频原始内容获取（至结构合并，不含 data-juicer）；可选 LLM 插件",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="处理单个视频")
    run_p.add_argument("video", type=Path, help="输入视频路径")
    run_p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="输出目录；默认见 RAW_INGEST_OUTPUT_ROOT / autodl-tmp/raw-ingest",
    )
    run_p.add_argument(
        "--replace",
        action="store_true",
        help="输出目录已存在时：先写入临时 staging，校验通过后再删除旧目录并替换（推荐）",
    )
    run_p.add_argument(
        "--force-in-place",
        action="store_true",
        help="输出目录已存在时：直接在原目录覆盖（中断可能留下半成品；与 --replace 互斥）",
    )
    run_p.add_argument("--skip-audio", action="store_true", help="跳过抽 WAV（需已存在）")
    run_p.add_argument("--skip-whisperx", action="store_true")
    run_p.add_argument("--skip-slides", action="store_true")
    run_p.add_argument("--skip-mineru", action="store_true")
    run_p.add_argument(
        "--skip-merge",
        action="store_true",
        help="仍写校验报告但跳过写 lesson_merged.*",
    )
    run_p.add_argument(
        "--similarity",
        type=float,
        default=0.6,
        help="关键帧相似度阈值（越低越敏感）",
    )
    run_p.add_argument(
        "--keyframe-commit",
        choices=["immediate", "tail"],
        default="tail",
        help="tail（默认）=检测到相对上一关键帧大变后，等画面静止再保存，接近完整 PPT；"
        "immediate=相似度首次低于阈值即保存（偏早，适合硬切/无需等尾帧）",
    )
    run_p.add_argument(
        "--keyframe-inter-stability",
        type=float,
        default=0.93,
        help="tail 模式：相邻两次采样相似度 ≥ 该值视为画面稳定（0~1）",
    )
    run_p.add_argument(
        "--keyframe-min-stable-seconds",
        type=int,
        default=2,
        help="tail 模式：连续稳定达到该秒数才落盘",
    )
    run_p.add_argument(
        "--keyframe-max-transition-sec",
        type=float,
        default=90.0,
        help="tail 模式：单页过渡超过该时长则强制落盘",
    )
    run_p.add_argument("--max-frames", type=int, default=None, help="最多保留关键帧数")
    run_p.add_argument("--whisperx-model", default="large-v2", help="WhisperX 模型名")
    run_p.add_argument(
        "--device",
        default="cuda",
        choices=["cuda", "cpu"],
        help="WhisperX / 对齐所用设备",
    )
    run_p.add_argument("--batch-size", type=int, default=16)
    run_p.add_argument("--language", default="zh", help="WhisperX 语言提示，如 zh / en / auto")
    run_p.add_argument(
        "--mineru-backend",
        default=None,
        help="MinerU -b，如 pipeline；也可设环境变量 MINERU_BACKEND",
    )
    run_p.add_argument(
        "--mineru-extra",
        default=None,
        help="MinerU 额外参数（空格分隔，原样追加）",
    )
    run_p.add_argument(
        "--mineru-fail-fast",
        action="store_true",
        help="任一帧 MinerU 失败则停止",
    )
    run_p.add_argument(
        "--require-speech",
        action="store_true",
        help="校验：口播片段不得为空",
    )
    run_p.add_argument(
        "--require-visual-text",
        action="store_true",
        help="校验：至少一帧 MinerU 有非空正文",
    )

    run_p.set_defaults(func=cmd_run)

    llm_p = sub.add_parser(
        "llm",
        help="可选 LLM 插件（OpenAI 兼容 API，如 4zapi）：连接自检、摘要、质检提示",
    )
    llm_sub = llm_p.add_subparsers(dest="llm_cmd", required=True)

    ping_p = llm_sub.add_parser("ping", help="测试 API 连接（不打印密钥）")
    ping_p.add_argument("--model", default=None, help="覆盖模型 ID")
    ping_p.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="额外 .env 路径（后加载覆盖）",
    )
    ping_p.set_defaults(func=cmd_llm)

    sum_p = llm_sub.add_parser(
        "summarize",
        help="读取 lesson_merged.json，生成中文摘要 llm_summary.md",
    )
    sum_p.add_argument("lesson_dir", type=Path, help="含 lesson_merged.json 的目录")
    sum_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出 Markdown 路径（默认 <lesson_dir>/llm_summary.md）",
    )
    sum_p.add_argument("--model", default=None, help="覆盖模型 ID")
    sum_p.add_argument("--env-file", type=Path, default=None)
    sum_p.set_defaults(func=cmd_llm)

    iss_p = llm_sub.add_parser(
        "suggest-issues",
        help="根据合并 JSON 列出可能的数据质量问题（抽检辅助）",
    )
    iss_p.add_argument("lesson_dir", type=Path)
    iss_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="默认 <lesson_dir>/llm_quality_hints.md",
    )
    iss_p.add_argument("--model", default=None)
    iss_p.add_argument("--env-file", type=Path, default=None)
    iss_p.set_defaults(func=cmd_llm)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn is None:
        parser.print_help()
        return 1
    return int(fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
