from __future__ import annotations

from agent_os.context_builder import ContextCharBudget, ContextBuilder
from agent_os.context_diagnostics import (
    build_context_diagnostics,
    format_context_diagnostics_markdown,
)


def test_context_diagnostics_reports_blocks_and_budget_status() -> None:
    builder = ContextBuilder(
        timezone_name="Asia/Shanghai",
        history_max_messages=2,
        include_runtime_context=False,
        context_char_budget=ContextCharBudget(max_total_chars=120),
        hard_total_budget=False,
        enable_token_estimate=False,
    )
    bundle = builder.build_turn_message(
        "请给我一个简短方案",
        entrypoint="cli",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        session_messages=[("user", "上一轮"), ("assistant", "上一轮回复")],
        retrieved_context="<ordered_context><mem0_profile>偏好：先给结论</mem0_profile></ordered_context>",
    )

    diag = build_context_diagnostics(bundle)
    data = diag.to_dict()
    names = [b["name"] for b in data["blocks"]]

    assert data["total_chars"] == len(bundle.message)
    assert data["max_total_chars"] == 120
    assert data["budget_status"] in {"danger", "over_budget"}
    assert "external_recall" in names
    assert "recent_history" in names
    assert "current_user_message" in names
    assert any(s["name"] == "context_budget" for s in data["signals"])


def test_context_diagnostics_markdown_contains_table() -> None:
    builder = ContextBuilder(
        timezone_name="Asia/Shanghai",
        history_max_messages=0,
        include_runtime_context=False,
        enable_token_estimate=False,
    )
    bundle = builder.build_turn_message(
        "只列 3 点",
        entrypoint="api",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
    )

    text = format_context_diagnostics_markdown(build_context_diagnostics(bundle))

    assert "## Context Diagnostics" in text
    assert "| Block | Injected | Chars | Prompt % | Source | Note |" in text
    assert "`attention_anchor`" in text
    assert "`current_user_message`" in text
