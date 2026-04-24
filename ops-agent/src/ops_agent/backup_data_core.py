"""本地 data/ 候选文件打包（供 ``scripts/backup_data.py`` 与单测调用）。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def copy_data_candidates_to_dir(*, repo_root: Path, dest_data: Path) -> list[str]:
    """将 ``repo_root/data`` 下预置候选项复制到 ``dest_data``，返回已复制项（相对 data/）。"""
    added: list[str] = []
    data = repo_root / "data"
    if not data.is_dir():
        return added
    names = [
        "hindsight.jsonl",
        "local_memory.json",
        "agno_session.db",
        "asset_store.lancedb",
    ]
    dest_data.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = data / name
        if not src.exists():
            continue
        dst = dest_data / name
        if src.is_file():
            shutil.copy2(src, dst)
            added.append(name)
        elif src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            added.append(f"{name}/")
    return added


def run_local_data_backup(
    *,
    repo_root: Path,
    output_dir: Path,
    name_prefix: str = "ops_agent_data",
) -> tuple[Path, list[str]]:
    """
    在 ``output_dir`` 下生成 ``{name_prefix}_UTC时间戳.zip``，返回 ``(zip路径, 条目列表)``。
    若无任何可备份项则返回 ``("", [])`` 且调用方应视为失败。
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = f"{name_prefix}_{ts}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp) / "bundle"
        added = copy_data_candidates_to_dir(repo_root=repo_root, dest_data=tmp_root / "data")
        if not added:
            return Path(), []
        archive_base = output_dir / base_name
        path = shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=tmp_root.parent,
            base_dir=tmp_root.name,
        )
    return Path(path), added


def backup_main(argv: list[str] | None = None) -> int:
    """CLI 入口（``python -m ops_agent.backup_data_core`` 或脚本包装）。"""
    import argparse

    p = argparse.ArgumentParser(description="打包 ops-agent 本地 data/ 候选文件")
    p.add_argument("--repo-root", type=Path, default=None, help="仓库根（含 data/），默认自动推断")
    p.add_argument("--output-dir", type=Path, default=None, help="默认 <repo>/backups")
    p.add_argument("--name-prefix", default="ops_agent_data")
    args = p.parse_args(argv)

    here = Path(__file__).resolve()
    # ``src/ops_agent/backup_data_core.py`` → 仓库根为 parents[2]
    root = args.repo_root or here.parents[2]
    out = args.output_dir or (root / "backups")
    path, added = run_local_data_backup(
        repo_root=root, output_dir=out, name_prefix=args.name_prefix
    )
    if not added:
        print("未找到可备份项（data/ 下候选均不存在）。", file=sys.stderr)
        return 1
    print(f"backup_ok path={path} entries={','.join(added)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(backup_main())
