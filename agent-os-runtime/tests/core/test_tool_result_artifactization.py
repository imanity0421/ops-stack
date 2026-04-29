from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_os.context_builder import (
    ContextBuilder,
    clean_history_messages_with_report,
)
from agent_os.context_diagnostics import build_context_diagnostics
from agent_os.knowledge.artifact_store import ArtifactStore
from agent_os.knowledge.tool_result_artifactizer import ToolResultArtifactizer


@dataclass(frozen=True)
class ToolMsg:
    role: str
    content: str
    tool_name: str = "retrieve_ordered_context"
    tool_use_id: str = "toolu_1"


class FailingArtifactizer:
    def artifactize(self, **_kwargs: object) -> object:
        raise RuntimeError("store unavailable")


def _artifactizer(tmp_path: Path, *, min_chars: int = 80) -> ToolResultArtifactizer:
    return ToolResultArtifactizer(
        store=ArtifactStore(tmp_path / "artifacts.db"),
        task_id="task_1",
        session_id="s1",
        min_chars=min_chars,
        digest_chars=80,
    )


def test_small_tool_result_is_not_artifactized(tmp_path: Path) -> None:
    report = clean_history_messages_with_report(
        [ToolMsg(role="tool", content="短工具结果")],
        max_messages=1,
        max_tool_output_chars=200,
        tool_result_artifactizer=_artifactizer(tmp_path, min_chars=80),
    )

    assert report.tool_outputs_artifactized_count == 0
    assert "<artifact " not in "\n".join(report.lines)
    assert "短工具结果" in "\n".join(report.lines)


def test_long_tool_result_is_persisted_and_replaced_with_artifact_ref(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    artifactizer = ToolResultArtifactizer(
        store=store,
        task_id="task_1",
        session_id="s1",
        min_chars=80,
        digest_chars=60,
    )
    long_result = "LONG_TOOL_RESULT_" + ("完整工具结果不应进入 prompt " * 30)

    report = clean_history_messages_with_report(
        [ToolMsg(role="tool", content=long_result, tool_use_id="toolu_long")],
        max_messages=1,
        max_tool_output_chars=120,
        tool_result_artifactizer=artifactizer,
    )
    joined = "\n".join(report.lines)
    artifacts = store.list_artifacts(task_id="task_1")

    assert report.tool_outputs_artifactized_count == 1
    assert len(artifacts) == 1
    assert artifacts[0].raw_content == long_result
    assert f'ref="{artifacts[0].artifact_id}"' in joined
    assert 'kind="tool_result"' in joined
    assert "完整工具结果不应进入 prompt " * 20 not in joined


def test_context_builder_prompt_uses_artifact_ref_and_trace_count(tmp_path: Path) -> None:
    long_result = "RAW_HISTORY_PAYLOAD_" + ("召回正文不应回灌 " * 40)
    builder = ContextBuilder(
        timezone_name="Asia/Shanghai",
        history_max_messages=1,
        include_runtime_context=False,
        enable_token_estimate=False,
    )

    bundle = builder.build_turn_message(
        "继续当前任务",
        entrypoint="cli",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        session_messages=[ToolMsg(role="tool", content=long_result, tool_use_id="toolu_trace")],
        tool_result_artifactizer=_artifactizer(tmp_path, min_chars=80),
    )

    assert "<artifact " in bundle.message
    assert 'kind="tool_result"' in bundle.message
    assert "召回正文不应回灌 " * 20 not in bundle.message
    assert "tool_artifactized=1" in bundle.trace.to_obs_log_line()
    diagnostics = build_context_diagnostics(bundle).to_dict()["artifact_diagnostics"]
    assert diagnostics["artifact_ref_count"] == 1
    assert diagnostics["tool_result_artifactized_count"] == 1
    assert diagnostics["source_artifactized_count"] == 0


def test_same_tool_result_reuses_existing_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    artifactizer = ToolResultArtifactizer(
        store=store,
        task_id="task_1",
        session_id="s1",
        min_chars=40,
    )
    long_result = "同一个工具结果 " * 20
    message = ToolMsg(role="tool", content=long_result, tool_use_id="toolu_same")

    first = clean_history_messages_with_report(
        [message],
        max_messages=1,
        tool_result_artifactizer=artifactizer,
    )
    second = clean_history_messages_with_report(
        [message],
        max_messages=1,
        tool_result_artifactizer=artifactizer,
    )

    assert first.lines == second.lines
    assert first.tool_outputs_artifactized_count == 1
    assert second.tool_outputs_artifactized_count == 1
    assert len(store.list_artifacts(task_id="task_1")) == 1


def test_artifactization_failure_falls_back_to_existing_fold() -> None:
    long_result = "FALLBACK_RAW_" + ("工具结果折叠 " * 40)

    report = clean_history_messages_with_report(
        [ToolMsg(role="tool", content=long_result)],
        max_messages=1,
        max_tool_output_chars=50,
        tool_result_artifactizer=FailingArtifactizer(),  # type: ignore[arg-type]
    )
    joined = "\n".join(report.lines)

    assert report.tool_outputs_artifactized_count == 0
    assert "工具结果已折叠" in joined
    assert "FALLBACK_RAW_" in joined
    assert "工具结果折叠 " * 10 not in joined
