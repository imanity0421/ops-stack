from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_golden_rules(path: Path | None) -> list[dict[str, Any]]:
    """从 JSON 数组加载规则；路径无效或格式错误则返回空列表。"""
    if path is None or not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and "pattern" in item and "message" in item:
            out.append(item)
    return out


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
