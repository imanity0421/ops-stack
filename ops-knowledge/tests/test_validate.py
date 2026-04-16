from pathlib import Path

from ops_knowledge.validate_merged import validate_lesson_merged


def test_validate_minimal_ok(minimal_merged: Path, lesson_schema_path: Path) -> None:
    ok, errs = validate_lesson_merged(minimal_merged, schema_path=lesson_schema_path)
    assert ok, errs


def test_validate_rejects_bad_version(minimal_merged: Path, lesson_schema_path: Path) -> None:
    import json

    data = json.loads(minimal_merged.read_text(encoding="utf-8"))
    data["schema_version"] = "2.0"
    minimal_merged.write_text(json.dumps(data), encoding="utf-8")
    ok, errs = validate_lesson_merged(minimal_merged, schema_path=lesson_schema_path)
    assert not ok
    assert errs
