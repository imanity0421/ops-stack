from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from html import escape
from typing import Literal

from agent_os.knowledge.artifact_store import ArtifactRecord, ArtifactStore, artifact_digest_fallback

SourceArtifactKind = Literal["source", "deliverable"]


def _text_or_empty(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _shorten(text: str, max_chars: int) -> str:
    t = _text_or_empty(text).strip()
    if len(t) <= max_chars:
        return t
    if max_chars <= 3:
        return t[:max_chars]
    return t[: max_chars - 3] + "..."


def _message_value(message: object, name: str) -> str:
    if isinstance(message, dict):
        return _text_or_empty(message.get(name)).strip()
    value = getattr(message, name, None)
    return _text_or_empty(value).strip()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class SourceArtifactizationState:
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceArtifactRef:
    artifact: ArtifactRecord
    stable_key: str
    replacement_text: str
    original_chars: int
    source_kind: SourceArtifactKind


class SourceArtifactizer:
    """Persist long user sources or assistant deliverables behind artifact refs."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        task_id: str,
        session_id: str,
        min_chars: int = 4_000,
        digest_chars: int = 240,
        state: SourceArtifactizationState | None = None,
    ) -> None:
        self._store = store
        self._task_id = task_id
        self._session_id = session_id
        self._min_chars = max(1, int(min_chars))
        self._digest_chars = max(40, int(digest_chars))
        self._state = state or SourceArtifactizationState()

    def artifactize(
        self,
        *,
        source_kind: SourceArtifactKind,
        content: str,
        source_name: str = "",
        source_id: str = "",
        message: object | None = None,
    ) -> SourceArtifactRef | None:
        raw = _text_or_empty(content)
        if len(raw.strip()) <= self._min_chars:
            return None
        kind: SourceArtifactKind = "deliverable" if source_kind == "deliverable" else "source"
        stable_key = self._stable_key(
            source_kind=kind,
            content=raw,
            source_name=source_name,
            source_id=source_id,
            message=message,
        )
        cached = self._state.replacements.get(stable_key)
        if cached:
            artifact = self._store.find_artifact_by_stable_key(stable_key)
            if artifact is not None:
                return SourceArtifactRef(
                    artifact=artifact,
                    stable_key=stable_key,
                    replacement_text=cached,
                    original_chars=len(raw),
                    source_kind=kind,
                )

        digest = artifact_digest_fallback(raw, max_chars=self._digest_chars)
        artifact = self._store.create_artifact(
            task_id=self._task_id,
            session_id=self._session_id,
            raw_content=raw,
            digest=digest,
            stable_key=stable_key,
        )
        replacement = self._replacement_text(
            artifact=artifact,
            source_kind=kind,
            source_name=source_name,
            original_chars=len(raw),
        )
        self._state.replacements[stable_key] = replacement
        return SourceArtifactRef(
            artifact=artifact,
            stable_key=stable_key,
            replacement_text=replacement,
            original_chars=len(raw),
            source_kind=kind,
        )

    def _stable_key(
        self,
        *,
        source_kind: SourceArtifactKind,
        content: str,
        source_name: str,
        source_id: str,
        message: object | None,
    ) -> str:
        explicit_id = _text_or_empty(source_id).strip()
        if not explicit_id and message is not None:
            for name in ("source_id", "message_id", "id"):
                explicit_id = _message_value(message, name)
                if explicit_id:
                    break
        source = explicit_id or _content_hash(content)
        return "source:" + _content_hash(
            "\x00".join(
                (
                    self._task_id,
                    self._session_id,
                    source_kind,
                    _text_or_empty(source_name).strip(),
                    source,
                )
            )
        )

    def _replacement_text(
        self,
        *,
        artifact: ArtifactRecord,
        source_kind: SourceArtifactKind,
        source_name: str,
        original_chars: int,
    ) -> str:
        name_attr = (
            f' source_name="{escape(source_name, quote=True)}"'
            if _text_or_empty(source_name).strip()
            else ""
        )
        return (
            f'<artifact ref="{escape(artifact.artifact_id, quote=True)}" '
            f'kind="{escape(source_kind, quote=True)}" task_id="{escape(artifact.task_id, quote=True)}"'
            f"{name_attr} "
            f'original_chars="{original_chars}" '
            f'digest_status="{escape(artifact.digest_status, quote=True)}">'
            f"<digest>{escape(_shorten(artifact.ref_digest, self._digest_chars), quote=False)}</digest>"
            "</artifact>"
        )
