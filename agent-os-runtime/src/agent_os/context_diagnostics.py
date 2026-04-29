from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_os.context_builder import ContextBundle, ContextTraceBlock


_CURRENT_USER_RE = re.compile(
    r"<current_user_message>\s*(?P<body>.*?)\s*</current_user_message>",
    flags=re.DOTALL,
)
_ESTIMATED_TOKENS_RE = re.compile(r"(?:^|,)estimated_tokens=(?P<value>\d+)")
_MAX_TOTAL_RE = re.compile(r"(?:^|,)max_total=(?P<value>\d+)")

_PRIMARY_CONTEXT_BLOCKS = (
    "runtime_context",
    "external_recall",
    "working_memory",
    "artifact_refs",
    "recent_history",
    "attention_anchor",
    "current_user_message",
)
_WARNING_RATIO = 0.75
_DANGER_RATIO = 0.90
_CURRENT_USER_HIGH_RATIO = 0.70


@dataclass(frozen=True)
class ContextBlockDiagnostic:
    name: str
    chars: int
    injected: bool
    source: str = ""
    note: str = ""
    percent_of_prompt: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "chars": self.chars,
            "injected": self.injected,
            "source": self.source,
            "note": self.note,
            "percent_of_prompt": round(self.percent_of_prompt, 4),
        }


@dataclass(frozen=True)
class ContextBudgetGuard:
    max_total_chars: int | None
    used_chars: int
    chars_left: int | None
    usage_ratio: float | None
    percent_left: float | None
    status: str
    is_above_warning_threshold: bool
    is_above_danger_threshold: bool
    is_at_blocking_limit: bool
    current_user_chars: int
    current_user_ratio: float | None
    current_user_high_ratio: bool
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_total_chars": self.max_total_chars,
            "used_chars": self.used_chars,
            "chars_left": self.chars_left,
            "usage_ratio": round(self.usage_ratio, 4) if self.usage_ratio is not None else None,
            "percent_left": round(self.percent_left, 2) if self.percent_left is not None else None,
            "status": self.status,
            "is_above_warning_threshold": self.is_above_warning_threshold,
            "is_above_danger_threshold": self.is_above_danger_threshold,
            "is_at_blocking_limit": self.is_at_blocking_limit,
            "current_user_chars": self.current_user_chars,
            "current_user_ratio": round(self.current_user_ratio, 4)
            if self.current_user_ratio is not None
            else None,
            "current_user_high_ratio": self.current_user_high_ratio,
            "recommendations": list(self.recommendations),
        }


@dataclass(frozen=True)
class ContextDiagnostics:
    total_chars: int
    injected_chars: int
    estimated_tokens: int | None
    max_total_chars: int | None
    budget_status: str
    budget_guard: ContextBudgetGuard
    blocks: list[ContextBlockDiagnostic] = field(default_factory=list)
    signals: list[ContextBlockDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_chars": self.total_chars,
            "injected_chars": self.injected_chars,
            "estimated_tokens": self.estimated_tokens,
            "max_total_chars": self.max_total_chars,
            "budget_status": self.budget_status,
            "budget_guard": self.budget_guard.to_dict(),
            "blocks": [b.to_dict() for b in self.blocks],
            "signals": [b.to_dict() for b in self.signals],
        }


def _parse_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text or "")
    if not match:
        return None
    try:
        return int(match.group("value"))
    except (TypeError, ValueError):
        return None


def _current_user_chars(message: str) -> int:
    match = _CURRENT_USER_RE.search(message or "")
    if not match:
        return 0
    return len(match.group("body").strip())


def _diagnostic_from_block(block: ContextTraceBlock, *, total_chars: int) -> ContextBlockDiagnostic:
    pct = (block.chars / max(1, total_chars)) if block.chars > 0 else 0.0
    return ContextBlockDiagnostic(
        name=block.name,
        chars=block.chars,
        injected=block.injected,
        source=block.source,
        note=block.note,
        percent_of_prompt=pct,
    )


def _budget_status(*, total_chars: int, max_total_chars: int | None) -> str:
    if not max_total_chars or max_total_chars <= 0:
        return "unbounded"
    ratio = total_chars / max(1, max_total_chars)
    if ratio > 1.0:
        return "over_budget"
    if ratio >= 0.9:
        return "danger"
    if ratio >= 0.75:
        return "warning"
    return "ok"


def _build_budget_guard(
    *,
    total_chars: int,
    max_total_chars: int | None,
    current_user_chars: int,
) -> ContextBudgetGuard:
    if not max_total_chars or max_total_chars <= 0:
        recommendations = [
            "No max_total_chars configured; /context can observe usage but cannot preflight a limit."
        ]
        return ContextBudgetGuard(
            max_total_chars=None,
            used_chars=total_chars,
            chars_left=None,
            usage_ratio=None,
            percent_left=None,
            status="unbounded",
            is_above_warning_threshold=False,
            is_above_danger_threshold=False,
            is_at_blocking_limit=False,
            current_user_chars=current_user_chars,
            current_user_ratio=None,
            current_user_high_ratio=False,
            recommendations=recommendations,
        )

    denominator = max(1, max_total_chars)
    usage_ratio = total_chars / denominator
    chars_left = max_total_chars - total_chars
    percent_left = max(0.0, ((max_total_chars - total_chars) / denominator) * 100)
    status = _budget_status(total_chars=total_chars, max_total_chars=max_total_chars)
    current_user_ratio = current_user_chars / denominator
    current_user_high_ratio = current_user_ratio >= _CURRENT_USER_HIGH_RATIO
    is_above_warning = usage_ratio >= _WARNING_RATIO
    is_above_danger = usage_ratio >= _DANGER_RATIO
    is_blocking = usage_ratio > 1.0

    recommendations: list[str] = []
    if is_blocking:
        recommendations.append(
            "Prompt exceeds configured context budget; run with hard budget or reduce history/recall before calling the model."
        )
    elif is_above_danger:
        recommendations.append(
            "Prompt is near the configured budget; prefer reducing recent history or external recall before this grows further."
        )
    elif is_above_warning:
        recommendations.append(
            "Prompt is above the warning threshold; inspect the largest blocks before long-running work."
        )
    if current_user_high_ratio:
        recommendations.append(
            "Current user message dominates the budget and is never truncated by ContextBuilder; ask for smaller input or move source material into assets."
        )

    return ContextBudgetGuard(
        max_total_chars=max_total_chars,
        used_chars=total_chars,
        chars_left=chars_left,
        usage_ratio=usage_ratio,
        percent_left=percent_left,
        status=status,
        is_above_warning_threshold=is_above_warning,
        is_above_danger_threshold=is_above_danger,
        is_at_blocking_limit=is_blocking,
        current_user_chars=current_user_chars,
        current_user_ratio=current_user_ratio,
        current_user_high_ratio=current_user_high_ratio,
        recommendations=recommendations,
    )


def build_context_diagnostics(bundle: ContextBundle) -> ContextDiagnostics:
    """Build a stable `/context`-style diagnostic payload from ContextBuilder output."""

    message = bundle.message or ""
    total_chars = len(message)
    blocks: list[ContextBlockDiagnostic] = []
    signals: list[ContextBlockDiagnostic] = []
    estimated_tokens: int | None = None
    max_total_chars: int | None = None
    seen_primary: set[str] = set()
    current_user_chars = _current_user_chars(message)

    for block in bundle.trace.blocks:
        diag = _diagnostic_from_block(block, total_chars=total_chars)
        if block.name in _PRIMARY_CONTEXT_BLOCKS:
            blocks.append(diag)
            seen_primary.add(block.name)
        else:
            signals.append(diag)
        if block.name == "token_estimate":
            estimated_tokens = _parse_int(_ESTIMATED_TOKENS_RE, block.note)
        if block.name == "context_budget":
            parsed = _parse_int(_MAX_TOTAL_RE, block.note)
            if parsed is not None:
                max_total_chars = parsed

    if "current_user_message" not in seen_primary:
        blocks.append(
            ContextBlockDiagnostic(
                name="current_user_message",
                chars=current_user_chars,
                injected=current_user_chars > 0,
                source="context_builder",
                note="final_user_message",
                percent_of_prompt=(current_user_chars / max(1, total_chars))
                if current_user_chars
                else 0.0,
            )
        )

    order = {name: i for i, name in enumerate(_PRIMARY_CONTEXT_BLOCKS)}
    blocks.sort(key=lambda b: order.get(b.name, len(order)))
    injected_chars = sum(b.chars for b in blocks if b.injected)
    budget_guard = _build_budget_guard(
        total_chars=total_chars,
        max_total_chars=max_total_chars,
        current_user_chars=current_user_chars,
    )
    return ContextDiagnostics(
        total_chars=total_chars,
        injected_chars=injected_chars,
        estimated_tokens=estimated_tokens,
        max_total_chars=max_total_chars,
        budget_status=budget_guard.status,
        budget_guard=budget_guard,
        blocks=blocks,
        signals=signals,
    )


def format_context_diagnostics_markdown(diag: ContextDiagnostics) -> str:
    token_text = str(diag.estimated_tokens) if diag.estimated_tokens is not None else "unavailable"
    budget_text = str(diag.max_total_chars) if diag.max_total_chars is not None else "unbounded"
    lines = [
        "## Context Diagnostics",
        "",
        f"- total_chars: {diag.total_chars}",
        f"- injected_chars: {diag.injected_chars}",
        f"- estimated_tokens: {token_text}",
        f"- max_total_chars: {budget_text}",
        f"- budget_status: {diag.budget_status}",
        f"- percent_left: {diag.budget_guard.percent_left if diag.budget_guard.percent_left is not None else 'unbounded'}",
        f"- current_user_chars: {diag.budget_guard.current_user_chars}",
        "",
        "| Block | Injected | Chars | Prompt % | Source | Note |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for block in diag.blocks:
        pct = f"{block.percent_of_prompt * 100:.1f}%"
        lines.append(
            f"| `{block.name}` | {str(block.injected).lower()} | {block.chars} | "
            f"{pct} | `{block.source or '-'}` | {block.note or '-'} |"
        )
    if diag.signals:
        lines.extend(["", "### Signals", ""])
        for signal in diag.signals:
            lines.append(
                f"- `{signal.name}`: chars={signal.chars}, injected={str(signal.injected).lower()}, "
                f"source=`{signal.source or '-'}`, note={signal.note or '-'}"
            )
    if diag.budget_guard.recommendations:
        lines.extend(["", "### Budget Guard", ""])
        for item in diag.budget_guard.recommendations:
            lines.append(f"- {item}")
    return "\n".join(lines)
