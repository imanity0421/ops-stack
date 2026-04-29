from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_os.context_builder import ContextBuilder, clean_history_messages_with_report
from agent_os.context_diagnostics import build_context_diagnostics
from agent_os.knowledge.artifact_store import ArtifactStore
from agent_os.knowledge.source_artifactizer import SourceArtifactizer


@dataclass(frozen=True)
class Msg:
    role: str
    content: str
    message_id: str = "m1"


class FailingSourceArtifactizer:
    def artifactize(self, **_kwargs: object) -> object:
        raise RuntimeError("store unavailable")


def _artifactizer(tmp_path: Path, *, min_chars: int = 80) -> SourceArtifactizer:
    return SourceArtifactizer(
        store=ArtifactStore(tmp_path / "artifacts.db"),
        task_id="task_1",
        session_id="s1",
        min_chars=min_chars,
        digest_chars=80,
    )


def test_small_user_source_is_not_artifactized(tmp_path: Path) -> None:
    report = clean_history_messages_with_report(
        [Msg(role="user", content="短素材")],
        max_messages=1,
        max_content_chars=200,
        source_artifactizer=_artifactizer(tmp_path),
    )

    assert report.source_artifactized_count == 0
    assert "<artifact " not in "\n".join(report.lines)
    assert "短素材" in "\n".join(report.lines)


def test_long_user_source_history_is_replaced_with_artifact_ref(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    artifactizer = SourceArtifactizer(
        store=store,
        task_id="task_1",
        session_id="s1",
        min_chars=80,
        digest_chars=60,
    )
    long_source = "USER_SOURCE_PAYLOAD_" + ("用户上传长素材不应完整回灌 " * 30)

    report = clean_history_messages_with_report(
        [Msg(role="user", content=long_source, message_id="user-source-1")],
        max_messages=1,
        max_content_chars=120,
        source_artifactizer=artifactizer,
    )
    joined = "\n".join(report.lines)
    artifacts = store.list_artifacts(task_id="task_1")

    assert report.source_artifactized_count == 1
    assert len(artifacts) == 1
    assert artifacts[0].raw_content == long_source
    assert f'ref="{artifacts[0].artifact_id}"' in joined
    assert 'kind="source"' in joined
    assert "用户上传长素材不应完整回灌 " * 20 not in joined


def test_long_assistant_deliverable_history_is_replaced_with_artifact_ref(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    deliverable = "ASSISTANT_DELIVERABLE_" + ("长交付正文不应完整回灌 " * 30)

    report = clean_history_messages_with_report(
        [Msg(role="assistant", content=deliverable, message_id="assistant-deliverable-1")],
        max_messages=1,
        max_content_chars=120,
        source_artifactizer=SourceArtifactizer(
            store=store,
            task_id="task_1",
            session_id="s1",
            min_chars=80,
            digest_chars=60,
        ),
    )
    joined = "\n".join(report.lines)

    assert report.source_artifactized_count == 1
    assert 'kind="deliverable"' in joined
    assert len(store.list_artifacts(task_id="task_1")) == 1
    assert "长交付正文不应完整回灌 " * 20 not in joined


def test_current_user_long_source_can_be_artifactized_in_prompt(tmp_path: Path) -> None:
    builder = ContextBuilder(
        timezone_name="Asia/Shanghai",
        history_max_messages=0,
        include_runtime_context=False,
        enable_token_estimate=False,
    )
    current = "CURRENT_USER_SOURCE_" + ("当前用户长素材不应完整进入 prompt " * 40)

    bundle = builder.build_turn_message(
        current,
        entrypoint="cli",
        client_id="c1",
        user_id=None,
        skill_id="default_agent",
        source_artifactizer=_artifactizer(tmp_path, min_chars=80),
    )

    assert "<current_user_message>" in bundle.message
    assert "<artifact " in bundle.message
    assert 'kind="source"' in bundle.message
    assert "当前用户长素材不应完整进入 prompt " * 20 not in bundle.message
    assert "current_user_source_artifact" in bundle.trace.to_obs_log_line()
    diagnostics = build_context_diagnostics(bundle).to_dict()["artifact_diagnostics"]
    assert diagnostics["artifact_ref_count"] == 1
    assert diagnostics["source_artifactized_count"] == 1
    assert diagnostics["current_user_source_artifactized"] is True


def test_same_source_reuses_existing_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    artifactizer = SourceArtifactizer(
        store=store,
        task_id="task_1",
        session_id="s1",
        min_chars=40,
    )
    content = "同一份长素材 " * 20
    message = Msg(role="user", content=content, message_id="source-same")

    first = clean_history_messages_with_report(
        [message],
        max_messages=1,
        source_artifactizer=artifactizer,
    )
    second = clean_history_messages_with_report(
        [message],
        max_messages=1,
        source_artifactizer=artifactizer,
    )

    assert first.lines == second.lines
    assert first.source_artifactized_count == 1
    assert second.source_artifactized_count == 1
    assert len(store.list_artifacts(task_id="task_1")) == 1


def test_source_artifactizer_uses_fallback_digest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.db")
    content = "fallback digest source " * 20

    ref = SourceArtifactizer(
        store=store,
        task_id="task_1",
        session_id="s1",
        min_chars=40,
        digest_chars=50,
    ).artifactize(source_kind="source", content=content, source_name="manual_upload")

    assert ref is not None
    assert ref.artifact.digest_status == "built"
    assert ref.artifact.digest is not None
    assert len(ref.artifact.digest) <= 50
    assert ref.artifact.digest in ref.replacement_text


def test_source_artifactization_failure_falls_back_to_existing_shortening() -> None:
    long_source = "FALLBACK_SOURCE_" + ("长素材折叠 " * 40)

    report = clean_history_messages_with_report(
        [Msg(role="user", content=long_source)],
        max_messages=1,
        max_content_chars=50,
        source_artifactizer=FailingSourceArtifactizer(),  # type: ignore[arg-type]
    )
    joined = "\n".join(report.lines)

    assert report.source_artifactized_count == 0
    assert "FALLBACK_SOURCE_" in joined
    assert "长素材折叠 " * 20 not in joined
