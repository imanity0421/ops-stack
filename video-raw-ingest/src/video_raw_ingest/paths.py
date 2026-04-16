"""输出路径解析：对齐 video-asset-pipeline 的镜像习惯（RAW_INGEST_*）。"""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    # src/video_raw_ingest/paths.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def _default_output_base_dir() -> Path:
    if os.environ.get("RAW_INGEST_REPO_OUTPUT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return _repo_root() / "output"
    autodl_tmp = Path("/root/autodl-tmp")
    if autodl_tmp.is_dir():
        return autodl_tmp / "raw-ingest"
    return _repo_root() / "output"


def _implicit_mirror_root(video_path: Path) -> Path | None:
    marker = Path("/root/autodl-tmp")
    try:
        if not marker.is_dir():
            return None
        root = marker.resolve()
        video_path.resolve().relative_to(root)
    except ValueError:
        return None
    out_root = (root / "raw-ingest").resolve()
    if out_root.is_dir():
        try:
            video_path.resolve().relative_to(out_root)
            return None
        except ValueError:
            pass
    return root


def _parent_parts_relative_to_input_root(
    video: Path, input_root: Path
) -> tuple[str, ...] | None:
    try:
        root = input_root.expanduser().resolve()
        if not root.is_dir():
            return None
        parent = video.resolve().parent
        rel = parent.relative_to(root)
    except (ValueError, OSError):
        return None
    if rel == Path("."):
        return ()
    return rel.parts


def default_output_dir_for_video(video_path: Path) -> Path:
    """
    未指定 -o 时的输出目录：<输出根>/<镜像子路径>/<视频主文件名>/。
    输出根：RAW_INGEST_OUTPUT_ROOT，否则见 _default_output_base_dir。
    """
    stem = video_path.stem
    env_root = os.environ.get("RAW_INGEST_OUTPUT_ROOT", "").strip()
    base = (
        Path(env_root).expanduser().resolve()
        if env_root
        else _default_output_base_dir()
    )

    input_raw = (
        os.environ.get("RAW_INGEST_INPUT_ROOT", "").strip()
        or os.environ.get("RAW_INGEST_BATCH_INPUT", "").strip()
    )
    mirror_root: Path | None = Path(input_raw) if input_raw else None
    if mirror_root is None:
        mirror_root = _implicit_mirror_root(video_path)

    if mirror_root is not None:
        parts = _parent_parts_relative_to_input_root(video_path, mirror_root)
        if parts is not None:
            out = base
            for p in parts:
                out = out / p
            return out / stem

    return base / stem
