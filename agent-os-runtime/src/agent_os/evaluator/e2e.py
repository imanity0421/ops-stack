from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_os.evaluator.golden import check_violations, load_golden_rules


@dataclass
class E2EEvalReport:
    """端到端抽检结果（不调用 LLM，仅规则）。"""

    name: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    assistant_turns_checked: int = 0


def run_e2e_eval_from_dict(case: dict[str, Any]) -> E2EEvalReport:
    """
    case 字段：
    - name: str
    - assistant_turns: list[str]（必填）
    - golden_rules: list[dict] 或 golden_rules_path: str（相对/绝对路径）
    """
    name = str(case.get("name", "unnamed"))
    turns = case.get("assistant_turns")
    if not isinstance(turns, list) or not turns:
        return E2EEvalReport(name=name, passed=False, violations=["缺少 assistant_turns"])

    rules_path = case.get("golden_rules_path")
    if isinstance(rules_path, str) and rules_path:
        rules = load_golden_rules(Path(rules_path))
    else:
        raw = case.get("golden_rules")
        rules = list(raw) if isinstance(raw, list) else []

    violations: list[str] = []
    for t in turns:
        if not isinstance(t, str):
            continue
        violations.extend(check_violations(t, rules))

    # 整段合并再扫一遍（跨行模式）
    merged = "\n".join(str(x) for x in turns if isinstance(x, str))
    violations.extend(check_violations(merged, rules))

    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for v in violations:
        if v not in seen:
            seen.add(v)
            uniq.append(v)

    return E2EEvalReport(
        name=name,
        passed=len(uniq) == 0,
        violations=uniq,
        assistant_turns_checked=len([x for x in turns if isinstance(x, str)]),
    )


def run_e2e_eval_file(path: Path) -> E2EEvalReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("评测文件顶层须为 JSON 对象")
    return run_e2e_eval_from_dict(data)
