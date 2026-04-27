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
    "recent_history",
    "attention_anchor",
    "current_user_message",
)


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
class ContextDiagnostics:
    total_chars: int
    injected_chars: int
    estimated_tokens: int | None
    max_total_chars: int | None
    budget_status: str
    blocks: list[ContextBlockDiagnostic] = field(default_factory=list)
    signals: list[ContextBlockDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_chars": self.total_chars,
            "injected_chars": self.injected_chars,
            "estimated_tokens": self.estimated_tokens,
            "max_total_chars": self.max_total_chars,
            "budget_status": self.budget_status,
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


def build_context_diagnostics(bundle: ContextBundle) -> ContextDiagnostics:
    """Build a stable `/context`-style diagnostic payload from ContextBuilder output."""

    message = bundle.message or ""
    total_chars = len(message)
    blocks: list[ContextBlockDiagnostic] = []
    signals: list[ContextBlockDiagnostic] = []
    estimated_tokens: int | None = None
    max_total_chars: int | None = None
    seen_primary: set[str] = set()

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
        current_chars = _current_user_chars(message)
        blocks.append(
            ContextBlockDiagnostic(
                name="current_user_message",
                chars=current_chars,
                injected=current_chars > 0,
                source="context_builder",
                note="final_user_message",
                percent_of_prompt=(current_chars / max(1, total_chars)) if current_chars else 0.0,
            )
        )

    order = {name: i for i, name in enumerate(_PRIMARY_CONTEXT_BLOCKS)}
    blocks.sort(key=lambda b: order.get(b.name, len(order)))
    injected_chars = sum(b.chars for b in blocks if b.injected)
    return ContextDiagnostics(
        total_chars=total_chars,
        injected_chars=injected_chars,
        estimated_tokens=estimated_tokens,
        max_total_chars=max_total_chars,
        budget_status=_budget_status(total_chars=total_chars, max_total_chars=max_total_chars),
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
    return "\n".join(lines)
