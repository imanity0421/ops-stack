"""输出目录解析：安全替换（staging）与「非空则须显式策略」。"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


def output_dir_is_nonempty(out: Path) -> bool:
    """目录存在且含任一子项即视为非空。"""
    if not out.is_dir():
        return False
    try:
        next(out.iterdir())
    except StopIteration:
        return False
    return True


def resolve_work_dir(
    final_out: Path,
    *,
    replace: bool,
    force_in_place: bool,
) -> tuple[Path, Path | None]:
    """
    返回 (work_dir, staging_dir_or_none)。

    - 若需安全替换且目标已非空：在同级建 staging，全程写入 staging，成功后删除 final_out 再移入。
    - force_in_place：直接写入 final_out（中断可能导致半成品覆盖）。
    - 两者皆否且目录非空：调用方应拒绝运行。
    """
    final_out = final_out.resolve()
    if not output_dir_is_nonempty(final_out):
        final_out.mkdir(parents=True, exist_ok=True)
        return final_out, None

    if force_in_place:
        final_out.mkdir(parents=True, exist_ok=True)
        return final_out, None

    if replace:
        pid = os.getpid()
        staging = final_out.parent / f"{final_out.name}.ingest-staging.{pid}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        return staging, staging

    raise FileExistsError(
        f"输出目录已存在且非空: {final_out}\n"
        "请使用 --replace（先写入临时目录，成功后再替换旧结果）或 "
        "--force-in-place（直接覆盖，中断可能留下半成品）。"
    )


def promote_staging_to_final(final_out: Path, staging: Path) -> None:
    """校验通过后：删除旧 final_out，将 staging 移为 final_out。"""
    final_out = final_out.resolve()
    staging = staging.resolve()
    if not staging.is_dir():
        raise FileNotFoundError(f"staging 不存在: {staging}")
    if final_out.exists():
        shutil.rmtree(final_out, ignore_errors=False)
    shutil.move(str(staging), str(final_out))


def backup_replaced_dir(final_out: Path) -> Path | None:
    """
    可选：将将被删除的目录先改名为 .replaced.<ts>（在 promote 前由调用方决定）。
    当前默认不保留备份；若需保留可调用本函数后再 promote。
    """
    final_out = final_out.resolve()
    if not final_out.is_dir():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    bak = final_out.parent / f"{final_out.name}.replaced.{ts}"
    shutil.move(str(final_out), str(bak))
    return bak
