from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def normalize_golden_rules(raw: Any) -> list[dict[str, Any]]:
    """Return only rules that can actually be evaluated."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or "pattern" not in item or "message" not in item:
            continue
        pat = item.get("pattern")
        if not isinstance(pat, str) or not pat:
            continue
        try:
            re.compile(pat)
        except re.error:
            continue
        out.append(item)
    return out


def load_golden_rules(path: Path | None) -> list[dict[str, Any]]:
    """从 JSON 数组加载规则；路径无效或格式错误则返回空列表。"""
    if path is None or not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return []
    return normalize_golden_rules(data)


def check_violations(text: str, rules: list[dict[str, Any]]) -> list[str]:
    """若文本匹配某条规则的 pattern，则记录一条可读违规说明。"""
    violations: list[str] = []
    for r in rules:
        pat = r.get("pattern")
        msg = r.get("message", "")
        rid = r.get("id", "")
        if not isinstance(pat, str) or not pat:
            continue
        try:
            if re.search(pat, text):
                label = f"[{rid}] " if rid else ""
                violations.append(f"{label}{msg}".strip())
        except re.error:
            continue
    return violations
