import json
from pathlib import Path

from ops_knowledge.manifest import build_manifest


def _minimal_lesson() -> dict:
    return {
        "schema_version": "1.0",
        "video": {"path": "x.mp4"},
        "speech": {
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
            "empty": False,
        },
        "visual": {"slides": [], "empty": True},
        "merged": {
            "timeline": [
                {"kind": "speech", "start_sec": 0.0, "end_sec": 1.0, "text": "hello"},
            ]
        },
    }


def test_manifest_scans(tmp_path: Path, lesson_schema_path: Path) -> None:
    sub = tmp_path / "lesson1"
    sub.mkdir()
    merged = sub / "lesson_merged.json"
    merged.write_text(json.dumps(_minimal_lesson()), encoding="utf-8")
    m = build_manifest(tmp_path, schema_path=lesson_schema_path)
    assert len(m.lessons) == 1
    assert m.lessons[0].valid
