from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ops_agent.evaluator.golden import check_violations, load_golden_rules

logger = logging.getLogger(__name__)


def load_skill_compliance_rules(
    skill_id: str,
    compliance_dir: Path | None,
) -> list[dict[str, Any]]:
    """
    每 skill 一份 JSON（与 ``OPS_GOLDEN_RULES_PATH`` 同格式：pattern + message + 可选 id）。
    文件路径：``<compliance_dir>/<skill_id>.json``
    未设置目录或文件不存在则返回空列表（不拦录入）。
    """
    if not compliance_dir or not compliance_dir.is_dir():
        return []
    p = compliance_dir / f"{skill_id}.json"
    if not p.is_file():
        return []
    return load_golden_rules(p)


def check_skill_compliance(text: str, skill_id: str, compliance_dir: Path | None) -> list[str]:
    """对文本做该 skill 的硬规则校验，返回违反说明列表（空=通过）。"""
    rules = load_skill_compliance_rules(skill_id, compliance_dir)
    return check_violations(text, rules)
