"""
幻灯片/静态画面关键帧抽取（思路对齐 extract-video-ppt / evp：每秒采样 + 帧间相似度）。

- **tail（默认）**：检测到相对上一关键帧的大幅变化后进入「过渡」，持续用**最新采样**作为候选；
  当**连续若干秒**内「相邻两次采样」高度相似（画面已静止）时再落盘，更接近**完整静止**后的 PPT。
- **immediate**：与上一张「已保存关键帧」的相似度首次低于阈值时，**立刻**保存当前采样帧
  （偏早，适合硬切、或明确不要等尾帧）。

输出带 timestamp_sec 的帧图与 keyframes.json；时间戳在 VFR 视频上为近似值。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

CommitMode = Literal["immediate", "tail"]


@dataclass
class KeyframeRecord:
    index: int
    timestamp_sec: float
    frame_relpath: str
    similarity_to_previous: float | None


def _compare_img_bgr_hist_correl(img1: np.ndarray, img2: np.ndarray, size=(256, 256)) -> float:
    """返回 [0,1]，越高越相似（与 OpenCV compareHist CORREL 一致方向）。"""
    a = cv2.resize(img1, size)
    b = cv2.resize(img2, size)
    scores: list[float] = []
    for c in range(3):
        h1 = cv2.calcHist([a], [c], None, [256], [0, 256])
        h2 = cv2.calcHist([b], [c], None, [256], [0, 256])
        cv2.normalize(h1, h1)
        cv2.normalize(h2, h2)
        corr = float(cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL))
        scores.append(corr)
    mean_c = float(np.mean(scores))
    # CORREL ∈ [-1,1]；映射到 [0,1] 便于与默认阈值 0.6 对齐
    return float(np.clip((mean_c + 1.0) / 2.0, 0.0, 1.0))


def extract_keyframes(
    video: Path,
    frames_dir: Path,
    *,
    similarity_threshold: float = 0.6,
    max_frames: int | None = None,
    fps_hint: float | None = None,
    duration_sec: float | None = None,
    commit_mode: CommitMode = "tail",
    inter_frame_stability: float = 0.93,
    min_stable_seconds: int = 2,
    max_transition_sec: float = 90.0,
) -> tuple[list[KeyframeRecord], dict[str, Any]]:
    """
    顺序读视频，按约 1 秒间隔采样帧。

    :param commit_mode: 默认 ``tail``（尾帧）；``immediate`` 为相似度首次低于阈值即保存。
    :param inter_frame_stability: tail 模式下，相邻两次采样相似度 ≥ 该值视为「这一秒内画面几乎不变」。
    :param min_stable_seconds: tail 模式下，连续满足上述条件的秒数达到该值才落盘。
    :param max_transition_sec: tail 模式下过渡过长则强制用当前候选帧落盘，避免卡死。
    :param fps_hint: 来自 ffprobe，用于 timestamp_sec = frame_index / fps
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")

    fps = float(fps_hint or cap.get(cv2.CAP_PROP_FPS) or 25.0)
    if fps <= 0:
        fps = 25.0
    sample_interval = max(1, int(round(fps)))

    if commit_mode == "immediate":
        records, meta = _extract_keyframes_immediate(
            cap,
            frames_dir,
            fps,
            sample_interval,
            similarity_threshold=similarity_threshold,
            max_frames=max_frames,
            duration_sec=duration_sec,
        )
    else:
        records, meta = _extract_keyframes_tail(
            cap,
            frames_dir,
            fps,
            sample_interval,
            similarity_threshold=similarity_threshold,
            max_frames=max_frames,
            duration_sec=duration_sec,
            inter_frame_stability=inter_frame_stability,
            min_stable_seconds=max(1, min_stable_seconds),
            max_transition_sec=max_transition_sec,
        )

    cap.release()

    meta["commit_mode"] = commit_mode
    if commit_mode == "tail":
        meta["inter_frame_stability"] = inter_frame_stability
        meta["min_stable_seconds"] = min_stable_seconds
        meta["max_transition_sec"] = max_transition_sec

    keyframes_path = frames_dir.parent / "keyframes.json"
    payload = {
        "video": str(video),
        "keyframes": [asdict(r) for r in records],
        "meta": meta,
    }
    keyframes_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records, meta


def _extract_keyframes_immediate(
    cap: cv2.VideoCapture,
    frames_dir: Path,
    fps: float,
    sample_interval: int,
    *,
    similarity_threshold: float,
    max_frames: int | None,
    duration_sec: float | None,
) -> tuple[list[KeyframeRecord], dict[str, Any]]:
    records: list[KeyframeRecord] = []
    last_kept: np.ndarray | None = None
    frame_idx = 0
    saved = 0
    meta: dict[str, Any] = {
        "fps_used": fps,
        "sample_interval_frames": sample_interval,
        "similarity_threshold": similarity_threshold,
    }

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_interval != 0:
            frame_idx += 1
            continue

        timestamp_sec = frame_idx / fps
        if duration_sec is not None and timestamp_sec > duration_sec + 1.0:
            break

        should_save = False
        sim: float | None = None
        if last_kept is None:
            should_save = True
        else:
            sim = _compare_img_bgr_hist_correl(frame, last_kept)
            if sim < similarity_threshold:
                should_save = True

        if should_save:
            if max_frames is not None and saved >= max_frames:
                break
            fname = f"frame_{saved:04d}_{timestamp_sec:.2f}s.jpg"
            fpath = frames_dir / fname
            if not cv2.imwrite(str(fpath), frame):
                raise RuntimeError(f"写入帧失败: {fpath}")
            rel = f"slides/frames/{fname}"
            rec = KeyframeRecord(
                index=saved,
                timestamp_sec=float(round(timestamp_sec, 4)),
                frame_relpath=rel,
                similarity_to_previous=sim,
            )
            records.append(rec)
            last_kept = frame.copy()
            saved += 1

        frame_idx += 1

    return records, meta


def _extract_keyframes_tail(
    cap: cv2.VideoCapture,
    frames_dir: Path,
    fps: float,
    sample_interval: int,
    *,
    similarity_threshold: float,
    max_frames: int | None,
    duration_sec: float | None,
    inter_frame_stability: float,
    min_stable_seconds: int,
    max_transition_sec: float,
) -> tuple[list[KeyframeRecord], dict[str, Any]]:
    """
    相对上一张已提交关键帧出现「大变」后暂不保存；用最新采样作候选，
    当连续 min_stable_seconds 次「与上一秒采样」足够相似时，再保存候选（尾帧/静止帧）。
    """
    records: list[KeyframeRecord] = []
    last_kept: np.ndarray | None = None
    prev_sampled: np.ndarray | None = None
    frame_idx = 0
    saved = 0

    in_transition = False
    stable_run = 0
    transition_start_ts = 0.0
    tail_candidate: np.ndarray | None = None
    last_sample_ts = 0.0

    meta: dict[str, Any] = {
        "fps_used": fps,
        "sample_interval_frames": sample_interval,
        "similarity_threshold": similarity_threshold,
    }

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_interval != 0:
            frame_idx += 1
            continue

        timestamp_sec = frame_idx / fps
        last_sample_ts = timestamp_sec
        if duration_sec is not None and timestamp_sec > duration_sec + 1.0:
            break

        sim_old: float | None = None
        if last_kept is None:
            if max_frames is not None and saved >= max_frames:
                break
            fname = f"frame_{saved:04d}_{timestamp_sec:.2f}s.jpg"
            fpath = frames_dir / fname
            if not cv2.imwrite(str(fpath), frame):
                raise RuntimeError(f"写入帧失败: {fpath}")
            rel = f"slides/frames/{fname}"
            records.append(
                KeyframeRecord(
                    index=saved,
                    timestamp_sec=float(round(timestamp_sec, 4)),
                    frame_relpath=rel,
                    similarity_to_previous=None,
                )
            )
            last_kept = frame.copy()
            prev_sampled = frame.copy()
            saved += 1
            frame_idx += 1
            continue

        sim_old = _compare_img_bgr_hist_correl(frame, last_kept)

        if not in_transition:
            if sim_old < similarity_threshold:
                in_transition = True
                stable_run = 0
                transition_start_ts = timestamp_sec
                tail_candidate = frame.copy()
            prev_sampled = frame.copy()
            frame_idx += 1
            continue

        # in_transition: 始终用最新采样作为尾帧候选
        tail_candidate = frame.copy()

        sim_prev = (
            _compare_img_bgr_hist_correl(frame, prev_sampled)
            if prev_sampled is not None
            else 0.0
        )
        if sim_prev >= inter_frame_stability:
            stable_run += 1
        else:
            stable_run = 0

        force = (timestamp_sec - transition_start_ts) >= max_transition_sec
        commit = stable_run >= min_stable_seconds or force

        if commit and tail_candidate is not None:
            if max_frames is not None and saved >= max_frames:
                break
            commit_ts = timestamp_sec
            fname = f"frame_{saved:04d}_{commit_ts:.2f}s.jpg"
            fpath = frames_dir / fname
            if not cv2.imwrite(str(fpath), tail_candidate):
                raise RuntimeError(f"写入帧失败: {fpath}")
            rel = f"slides/frames/{fname}"
            sim_to_prev_kept = _compare_img_bgr_hist_correl(tail_candidate, last_kept)
            records.append(
                KeyframeRecord(
                    index=saved,
                    timestamp_sec=float(round(commit_ts, 4)),
                    frame_relpath=rel,
                    similarity_to_previous=float(sim_to_prev_kept),
                )
            )
            last_kept = tail_candidate.copy()
            in_transition = False
            stable_run = 0
            saved += 1

        prev_sampled = frame.copy()
        frame_idx += 1

    # 视频结束仍卡在过渡：保存最后一帧候选（时间戳用最后一次成功采样的秒）
    if in_transition and tail_candidate is not None and last_kept is not None:
        if max_frames is None or saved < max_frames:
            commit_ts = last_sample_ts if last_sample_ts > 0 else transition_start_ts
            fname = f"frame_{saved:04d}_{commit_ts:.2f}s.jpg"
            fpath = frames_dir / fname
            if cv2.imwrite(str(fpath), tail_candidate):
                rel = f"slides/frames/{fname}"
                sim_to_prev_kept = _compare_img_bgr_hist_correl(tail_candidate, last_kept)
                records.append(
                    KeyframeRecord(
                        index=saved,
                        timestamp_sec=float(round(float(commit_ts), 4)),
                        frame_relpath=rel,
                        similarity_to_previous=float(sim_to_prev_kept),
                    )
                )

    return records, meta
