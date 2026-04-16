import importlib.util
from pathlib import Path

import pytest


def _ops_stack_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_repos():
    root = _ops_stack_root()
    spec = importlib.util.spec_from_file_location(
        "ops_stack_load_layout",
        root / "load_layout.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {root / 'load_layout.py'}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_repos(root)


@pytest.fixture
def repo_root() -> Path:
    """本包（ops-knowledge）仓库根目录。"""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def coding_root(repo_root: Path) -> Path:
    """tests 上级为仓库根，再上为 ops-stack 总目录。"""
    return repo_root.parent


@pytest.fixture
def lesson_schema_path(repo_root: Path, coding_root: Path) -> Path:
    """优先使用本仓库 fixtures（CI 无 sibling 时可用），否则回退到 ops-stack 内 ① 仓库。"""
    fixture = repo_root / "tests" / "fixtures" / "lesson_merged.schema.json"
    if fixture.is_file():
        return fixture
    repos = _load_repos()
    p = coding_root / repos["video_raw_ingest"] / "schema" / "lesson_merged.schema.json"
    if not p.is_file():
        pytest.skip(
            f"需要 tests/fixtures 或 ops-stack 内 {repos['video_raw_ingest']}/schema: {p}"
        )
    return p


@pytest.fixture
def minimal_merged(tmp_path: Path) -> Path:
    """通过 schema 的最小 lesson_merged.json。"""
    data = {
        "schema_version": "1.0",
        "video": {"path": "x.mp4"},
        "speech": {
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
            "empty": False,
        },
        "visual": {"slides": [], "empty": True},
        "merged": {
            "timeline": [
                {"kind": "speech", "start_sec": 0.0, "end_sec": 1.0, "text": "hello"},
            ]
        },
    }
    import json

    p = tmp_path / "lesson_merged.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p
