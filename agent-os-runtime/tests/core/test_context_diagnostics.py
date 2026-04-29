from __future__ import annotations

from agent_os.context_builder import ContextCharBudget, ContextBuilder
from agent_os.context_diagnostics import (
    build_context_diagnostics,
    format_context_diagnostics_markdown,
    normalize_resume_diagnostics,
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
    assert data["budget_guard"]["status"] == data["budget_status"]
    assert data["budget_guard"]["is_above_danger_threshold"] is True
    assert data["budget_guard"]["percent_left"] is not None
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


def test_context_diagnostics_budget_guard_flags_large_current_message() -> None:
    builder = ContextBuilder(
        timezone_name="Asia/Shanghai",
        history_max_messages=0,
        include_runtime_context=False,
        context_char_budget=ContextCharBudget(max_total_chars=300),
        enable_token_estimate=False,
    )
    bundle = builder.build_turn_message(
        "请处理以下超长材料：" + ("材料片段 " * 80),
        entrypoint="api",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
    )

    diag = build_context_diagnostics(bundle)
    guard = diag.to_dict()["budget_guard"]

    assert guard["is_at_blocking_limit"] is True
    assert guard["current_user_high_ratio"] is True
    assert guard["current_user_chars"] > 300
    assert any("Current user message dominates" in item for item in guard["recommendations"])


def test_gc9_trace7_context_diagnostics_normalizes_skill_fragment_fallback() -> None:
    builder = ContextBuilder(
        timezone_name="Asia/Shanghai",
        history_max_messages=0,
        include_runtime_context=False,
        enable_token_estimate=False,
    )
    bundle = builder.build_turn_message(
        "继续",
        entrypoint="api",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
    )
    resume = normalize_resume_diagnostics(
        {
            "resume_diagnostics": {
                "connect_or_fork": "fork",
                "decision_reason": ["forced_fork"],
                "forced_by_flag": True,
                "source_session_id": "s1",
                "target_session_id": "s2",
                "active_skill_id": "default_agent",
                "skill_fragment_skipped": True,
                "skill_fragment_skip_reason": "provider_missing",
            }
        }
    )

    diag = build_context_diagnostics(bundle, resume_diagnostics=resume)
    data = diag.to_dict()
    text = format_context_diagnostics_markdown(diag)

    assert data["resume_diagnostics"]["active_skill_id"] == "default_agent"
    assert data["resume_diagnostics"]["skill_fragment_skipped"] is True
    assert data["resume_diagnostics"]["skill_fragment_skip_reason"] == "provider_missing"
    assert "- active_skill_id: default_agent" in text
    assert "- skill_fragment_skipped: true" in text
    assert "- skill_fragment_skip_reason: provider_missing" in text
