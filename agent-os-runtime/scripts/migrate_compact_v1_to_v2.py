#!/usr/bin/env python3
"""CompactSummary v1 -> v2 一次性数据迁移脚本（Phase 9）。

Phase 9 把 ``CompactSummary`` 从三层 schema（core / business_writing_pack / skill_state）
收敛为两层（core / skill_state）——详见 ``docs/ARCHITECTURE.md`` §3.2 与 Phase 9 修订记录。

本脚本对 SQLite 中 ``compact_summaries`` 表里的 ``summary_json`` 列做就地迁移：
丢弃 ``business_writing_pack`` 键 + bump ``schema_version`` 到 ``"v2"`` +
同步 ``compact_summaries.schema_version`` 列。

在 ``agent-os-runtime`` 根目录执行::

  python scripts/migrate_compact_v1_to_v2.py --db-path data/agent_os/task_memory.sqlite --dry-run
  python scripts/migrate_compact_v1_to_v2.py --db-path data/agent_os/task_memory.sqlite

注：
- ``compact_summary_from_json`` 已内置 v1 -> v2 反序列化兼容（详见 ``src/agent_os/agent/compact.py``），
  即使不跑迁移脚本也不会 crash，但脚本能让 SQLite 中 ``schema_version`` 列与 JSON blob 内的版本字段
  保持一致，避免依赖运行时回退。
- 仅迁移 ``schema_version == "v1"`` 的行；v2 行原样跳过；schema_version 缺失的行按 v1 处理。
- 不删除任何行；只重写 ``summary_json`` 与 ``schema_version`` 两列。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def migrate(db_path: Path, *, dry_run: bool) -> dict[str, int]:
    """v1 -> v2 inline migration over compact_summaries.summary_json.

    Returns a stats dict with keys: scanned / migrated / skipped_v2 / errors.
    """
    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    stats = {"scanned": 0, "migrated": 0, "skipped_v2": 0, "errors": 0}
    try:
        rows = conn.execute(
            "SELECT session_id, task_id, summary_json, schema_version "
            "FROM compact_summaries"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        raise SystemExit(f"compact_summaries table not found in {db_path}: {exc}") from exc

    pending: list[tuple[str, str, str, str]] = []
    for row in rows:
        stats["scanned"] += 1
        raw = row["summary_json"]
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            stats["errors"] += 1
            continue
        if not isinstance(data, dict):
            stats["errors"] += 1
            continue
        ver = data.get("schema_version") or row["schema_version"]
        if ver == "v2":
            stats["skipped_v2"] += 1
            continue
        if ver not in (None, "", "v1"):
            stats["errors"] += 1
            continue
        data.pop("business_writing_pack", None)
        data["schema_version"] = "v2"
        new_json = json.dumps(data, ensure_ascii=False)
        pending.append((new_json, "v2", row["session_id"], row["task_id"]))
        stats["migrated"] += 1

    if dry_run:
        conn.close()
        return stats

    if pending:
        with conn:
            conn.executemany(
                "UPDATE compact_summaries SET summary_json = ?, schema_version = ? "
                "WHERE session_id = ? AND task_id = ?",
                pending,
            )
    conn.close()
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="CompactSummary v1 -> v2 数据迁移（Phase 9）")
    p.add_argument(
        "--db-path",
        type=Path,
        required=True,
        help="task_memory.sqlite 路径（含 compact_summaries 表）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描并打印统计，不写回数据",
    )
    args = p.parse_args()

    stats = migrate(args.db_path, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[{mode}] db={args.db_path} scanned={stats['scanned']} "
        f"migrated={stats['migrated']} skipped_v2={stats['skipped_v2']} "
        f"errors={stats['errors']}"
    )
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
