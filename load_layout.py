"""解析 ops-stack 下各子仓库的目录名。

默认与 ``ops-stack.toml`` 中 ``[repos]`` 一致；若你重命名了某个子文件夹，只需
修改该 TOML，或设置环境变量 ``OPS_STACK_REPO_<KEY>``（KEY 为 ``video_raw_ingest``、
``ops_knowledge`` 等的大写下划线形式，例如 ``OPS_STACK_REPO_OPS_KNOWLEDGE``）。

本模块位于 ``ops-stack`` 根目录，供 ``pipeline-demo``、``ops-knowledge/tests`` 等
通过 importlib 加载；勿安装为独立包。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef, import-not-found]
    except ModuleNotFoundError as e:  # pragma: no cover
        raise ImportError(
            "解析 ops-stack.toml 需要 Python 3.11+，或在当前环境执行: pip install tomli"
        ) from e

DEFAULT_REPOS: dict[str, str] = {
    "video_raw_ingest": "video-raw-ingest",
    "ops_knowledge": "ops-knowledge",
    "ops_distiller_forge": "ops-distiller-forge",
    "ops_agent": "ops-agent",
    "pipeline_demo": "pipeline-demo",
}

_ENV_PREFIX = "OPS_STACK_REPO_"


def _env_key_for_repo(repo_key: str) -> str:
    return _ENV_PREFIX + repo_key.upper()


def load_repos(ops_stack_root: Path) -> dict[str, str]:
    """合并内置默认值、``ops-stack.toml`` 与环境变量覆盖。"""
    out = dict(DEFAULT_REPOS)
    cfg = ops_stack_root / "ops-stack.toml"
    if cfg.is_file():
        data: dict[str, Any] = tomllib.loads(cfg.read_text(encoding="utf-8"))
        for k, v in (data.get("repos") or {}).items():
            if isinstance(v, str) and v.strip() and k in out:
                out[k] = v.strip()
    for key in list(out.keys()):
        ev = os.environ.get(_env_key_for_repo(key))
        if ev and ev.strip():
            out[key] = ev.strip()
    return out


def repo_path(ops_stack_root: Path, key: str) -> Path:
    if key not in DEFAULT_REPOS:
        raise KeyError(f"Unknown repo key: {key!r}, expected one of {sorted(DEFAULT_REPOS)}")
    repos = load_repos(ops_stack_root)
    return (ops_stack_root / repos[key]).resolve()
