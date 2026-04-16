"""交付质量辅助：基于本地 JSON 规则的正则抽检（可选）。"""

from ops_agent.evaluator.e2e import E2EEvalReport, run_e2e_eval_file, run_e2e_eval_from_dict
from ops_agent.evaluator.golden import check_violations, load_golden_rules

__all__ = [
    "check_violations",
    "load_golden_rules",
    "E2EEvalReport",
    "run_e2e_eval_file",
    "run_e2e_eval_from_dict",
]
