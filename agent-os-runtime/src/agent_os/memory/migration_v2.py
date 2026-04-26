from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_os.knowledge.group_id import system_graphiti_group_id
from agent_os.memory.models import CLIENT_SHARED_USER_ID

logger = logging.getLogger(__name__)


def _dedupe_memories(memories: list[Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in memories:
        if not isinstance(m, dict):
            continue
        text = m.get("text", "")
        meta = m.get("metadata") or {}
        if not isinstance(text, str):
            continue
        if not isinstance(meta, dict):
            meta = {}
        key = f"{text.strip()}\n{json.dumps(meta, sort_keys=True, ensure_ascii=False)}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": text, "metadata": meta})
    return out


def migrate_local_memory_v2(path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """
    将旧版 LocalMemoryBackend 的租户根桶 ``users[client_id]``（无 ``::``）
    合并到 ``users[f\"{client_id}::{CLIENT_SHARED_USER_ID}\"]``。

    旧版 ``mem_user_id(client, None) -> client_id``；新版公司共享写入 ``client::__client_shared__``。
    双读兼容仍可读旧桶，本迁移用于数据归一与后续运维清晰。
    """
    if not path.is_file():
        return {"status": "skipped", "reason": "file_not_found", "path": str(path)}

    try:
        raw = path.read_text(encoding="utf-8-sig")
        data: Any = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return {"status": "error", "reason": "invalid_json", "error": str(e)}

    if not isinstance(data, dict):
        return {"status": "error", "reason": "root_not_object"}

    users = data.get("users")
    if not isinstance(users, dict):
        return {"status": "error", "reason": "users_not_object"}

    moved: list[str] = []
    merged_counts: dict[str, int] = {}

    for key in list(users.keys()):
        if not isinstance(key, str):
            continue
        k = key.strip()
        if not k or "::" in k:
            continue
        if k == CLIENT_SHARED_USER_ID:
            continue

        src_bucket = users.get(key)
        if not isinstance(src_bucket, dict):
            continue
        src_mem = src_bucket.get("memories")
        if not isinstance(src_mem, list) or not src_mem:
            continue

        target_key = f"{k}::{CLIENT_SHARED_USER_ID}"
        tgt_bucket = users.get(target_key)
        if not isinstance(tgt_bucket, dict):
            users[target_key] = {"memories": []}
            tgt_bucket = users[target_key]

        tgt_mem = tgt_bucket.get("memories")
        if not isinstance(tgt_mem, list):
            tgt_mem = []
            tgt_bucket["memories"] = tgt_mem

        combined = _dedupe_memories(list(tgt_mem) + list(src_mem))
        merged_counts[target_key] = len(combined)
        tgt_bucket["memories"] = combined
        del users[key]
        moved.append(f"{key} -> {target_key}")

    if dry_run:
        return {
            "status": "dry_run",
            "path": str(path),
            "moved_buckets": moved,
            "would_merge_into": merged_counts,
        }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("migrate_local_memory_v2: wrote %s buckets_moved=%d", path, len(moved))
    return {"status": "ok", "path": str(path), "moved_buckets": moved, "merged_into": merged_counts}


def _split_legacy_graphiti_group_id(group_id: str) -> tuple[str, str] | None:
    if "__" not in group_id:
        return None
    left, right = group_id.rsplit("__", 1)
    if not left or not right:
        return None
    return left, right


def migrate_knowledge_jsonl_v2(
    path: Path,
    *,
    dry_run: bool = True,
    mode: str = "duplicate",
) -> dict[str, Any]:
    """
    将旧版 ``graphiti_group_id(client_id, skill_id)`` 行迁移为系统级 ``system_graphiti_group_id(skill_id)``。

    - ``duplicate``：保留原行并追加一行新 ``group_id``（最安全）。
    - ``replace``：仅写新 ``group_id``（破坏性，仅当你确认可丢弃旧分区键时）。
    """
    if mode not in ("duplicate", "replace"):
        raise ValueError("mode 须为 duplicate 或 replace")

    if not path.is_file():
        return {"status": "skipped", "reason": "file_not_found", "path": str(path)}

    lines_out: list[str] = []
    changed = 0
    skipped = 0
    try:
        raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        return {"status": "error", "reason": "invalid_jsonl", "error": str(e)}

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            lines_out.append(line)
            continue
        if not isinstance(row, dict):
            skipped += 1
            lines_out.append(line)
            continue
        gid = row.get("group_id")
        text = row.get("text")
        if not isinstance(gid, str) or not isinstance(text, str):
            skipped += 1
            lines_out.append(json.dumps(row, ensure_ascii=False))
            continue

        parts = _split_legacy_graphiti_group_id(gid)
        if parts is None:
            lines_out.append(json.dumps(row, ensure_ascii=False))
            continue
        _, skill_part = parts
        new_gid = system_graphiti_group_id(skill_part)
        if new_gid == gid:
            lines_out.append(json.dumps(row, ensure_ascii=False))
            continue

        if mode == "duplicate":
            lines_out.append(json.dumps(row, ensure_ascii=False))
            new_row = dict(row)
            new_row["group_id"] = new_gid
            lines_out.append(json.dumps(new_row, ensure_ascii=False))
        else:
            row["group_id"] = new_gid
            lines_out.append(json.dumps(row, ensure_ascii=False))
        changed += 1

    if dry_run:
        return {
            "status": "dry_run",
            "path": str(path),
            "mode": mode,
            "lines_changed": changed,
            "lines_skipped_malformed": skipped,
            "output_lines": len(lines_out),
        }

    path.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")
    logger.info(
        "migrate_knowledge_jsonl_v2: wrote %s changed=%d skipped=%d",
        path,
        changed,
        skipped,
    )
    return {
        "status": "ok",
        "path": str(path),
        "mode": mode,
        "lines_changed": changed,
        "lines_skipped_malformed": skipped,
        "output_lines": len(lines_out),
    }
