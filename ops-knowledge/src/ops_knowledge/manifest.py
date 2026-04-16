from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from ops_knowledge.schema_path import resolve_lesson_merged_schema_path
from ops_knowledge.validate_merged import validate_lesson_merged


@dataclass
class LessonEntry:
    """单课 handoff 条目。"""

    relpath: str
    sha256: str
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class HandbookHandoff:
    """供下游 DSPy / ops-agent 读取的制品清单。"""

    handoff_version: str = "1.0"
    created_utc: str = ""
    video_raw_ingest_schema_ref: str = ""
    lessons: list[LessonEntry] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "handoff_version": self.handoff_version,
                "created_utc": self.created_utc,
                "video_raw_ingest_schema_ref": self.video_raw_ingest_schema_ref,
                "lessons": [asdict(x) for x in self.lessons],
            },
            ensure_ascii=False,
            indent=2,
        )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    ingest_root: Path,
    *,
    schema_path: Path | None = None,
) -> HandbookHandoff:
    """扫描 ingest_root 下所有 lesson_merged.json。"""
    root = ingest_root.resolve()
    resolved_schema = resolve_lesson_merged_schema_path(schema_path)
    lessons: list[LessonEntry] = []
    for p in sorted(root.rglob("lesson_merged.json")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        digest = _sha256_file(p)
        ok, errs = validate_lesson_merged(p, schema_path=schema_path)
        lessons.append(LessonEntry(relpath=rel, sha256=digest, valid=ok, errors=errs[:20]))
    return HandbookHandoff(
        created_utc=datetime.now(timezone.utc).isoformat(),
        video_raw_ingest_schema_ref=str(resolved_schema),
        lessons=lessons,
    )
