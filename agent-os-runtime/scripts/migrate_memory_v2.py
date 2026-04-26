#!/usr/bin/env python3
"""Memory / 知识 JSONL V2 迁移脚本。

在 ``agent-os-runtime`` 根目录执行::

  python scripts/migrate_memory_v2.py local-memory --path data/local_memory.json --dry-run
  python scripts/migrate_memory_v2.py local-memory --path data/local_memory.json

  python scripts/migrate_memory_v2.py knowledge-jsonl --path path/to/k.jsonl --mode duplicate --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from agent_os.memory.migration_v2 import (  # noqa: E402
    migrate_knowledge_jsonl_v2,
    migrate_local_memory_v2,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Memory V2 数据迁移")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_loc = sub.add_parser("local-memory", help="迁移本地 Mem0 JSON（无 MEM0_API_KEY）")
    p_loc.add_argument("--path", type=Path, required=True, help="local_memory.json 路径")
    p_loc.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计划，不写回文件",
    )

    p_k = sub.add_parser("knowledge-jsonl", help="迁移 Graphiti JSONL fallback 的 group_id")
    p_k.add_argument("--path", type=Path, required=True, help="JSONL 路径")
    p_k.add_argument(
        "--mode",
        choices=("duplicate", "replace"),
        default="duplicate",
        help="duplicate 保留旧行并追加新 group_id；replace 原地替换",
    )
    p_k.add_argument("--dry-run", action="store_true")

    args = p.parse_args()
    if args.cmd == "local-memory":
        r = migrate_local_memory_v2(args.path, dry_run=bool(args.dry_run))
    else:
        r = migrate_knowledge_jsonl_v2(args.path, dry_run=bool(args.dry_run), mode=args.mode)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("status") in ("ok", "dry_run", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
