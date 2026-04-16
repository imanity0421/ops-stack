"""加载 .env；规则对齐 video-asset-pipeline stage_b。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    """
    优先 RAW_INGEST_REPO_ROOT；
    其次若当前工作目录存在 .env 则用 cwd（便于在仓库根执行）；
    否则按源码布局推断仓库根（src/video_raw_ingest/llm/ → 上三级）。
    """
    env = os.environ.get("RAW_INGEST_REPO_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / ".env").is_file():
        return cwd
    return Path(__file__).resolve().parents[3]


def load_env_files(repo_root: Path, extra_env_file: Path | None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        if extra_env_file or os.environ.get("RAW_INGEST_DOTENV"):
            print(
                "提示: 已指定外部 env 但未安装 python-dotenv，请 pip install python-dotenv",
                file=sys.stderr,
            )
        return

    env_local = repo_root / ".env"
    if env_local.is_file():
        load_dotenv(env_local)

    extra = os.environ.get("RAW_INGEST_DOTENV", "").strip()
    if extra:
        p = Path(extra).expanduser()
        if p.is_file():
            load_dotenv(p, override=True)

    if extra_env_file is not None:
        ef = extra_env_file.expanduser().resolve()
        if ef.is_file():
            load_dotenv(ef, override=True)
        else:
            print(f"警告: --env-file 无效，已忽略: {ef}", file=sys.stderr)


def get_openai_settings(cli_model: str | None) -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)。"""
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (
        (os.environ.get("OPENAI_API_BASE") or "").strip()
        or (os.environ.get("OPENAI_BASE_URL") or "").strip()
        or "https://api.openai.com/v1"
    )
    model = (cli_model or "").strip() or (
        (os.environ.get("OPENAI_CHAT_MODEL") or "").strip()
        or (os.environ.get("OPENAI_MODEL") or "").strip()
        or "gpt-4o"
    )
    return api_key, base, model
