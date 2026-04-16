"""WhisperX：口播转写 + 对齐时间戳。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _apply_windows_thread_safety_env() -> None:
    if sys.platform != "win32":
        return
    pairs = (
        ("OMP_NUM_THREADS", "1"),
        ("OPENBLAS_NUM_THREADS", "1"),
        ("MKL_NUM_THREADS", "1"),
        ("VECLIB_MAXIMUM_THREADS", "1"),
        ("NUMEXPR_NUM_THREADS", "1"),
        ("KMP_DUPLICATE_LIB_OK", "TRUE"),
    )
    for k, v in pairs:
        os.environ.setdefault(k, v)


def run_whisperx(
    wav_path: Path,
    out_dir: Path,
    *,
    model_name: str = "large-v2",
    device: str = "cuda",
    compute_type: str | None = None,
    batch_size: int = 16,
    language: str | None = "zh",
) -> dict[str, Any]:
    """
    返回标准化结构：{"language": str, "segments": [{"start","end","text"}, ...]}
    """
    _apply_windows_thread_safety_env()
    import torch
    import whisperx

    wav_path = wav_path.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"

    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    audio = whisperx.load_audio(str(wav_path))
    model = whisperx.load_model(
        model_name,
        device,
        compute_type=compute_type,
    )
    result = model.transcribe(audio, batch_size=batch_size, language=language)

    raw_segments = result.get("segments") or []
    if not raw_segments:
        payload = {
            "language": result.get("language") or language or "en",
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "segments": [],
        }
        (out_dir / "segments.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "raw_aligned.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    lang_code = result.get("language") or language or "en"
    model_a, metadata = whisperx.load_align_model(
        language_code=lang_code, device=device
    )
    aligned = whisperx.align(
        raw_segments,
        model_a,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )

    segments: list[dict[str, Any]] = []
    for seg in aligned.get("segments") or []:
        text = (seg.get("text") or "").strip()
        segments.append(
            {
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "text": text,
            }
        )

    payload: dict[str, Any] = {
        "language": lang_code,
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "segments": segments,
    }
    (out_dir / "segments.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "raw_aligned.json").write_text(
        json.dumps(aligned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload
