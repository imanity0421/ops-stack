from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ops_knowledge.schema_path import resolve_lesson_merged_schema_path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_lesson_merged(
    merged_path: Path,
    *,
    schema_path: Path | None = None,
) -> tuple[bool, list[str]]:
    """
    校验 lesson_merged.json。
    返回 (是否通过, 错误信息列表)。
    """
    schema = load_json(resolve_lesson_merged_schema_path(schema_path))
    data = load_json(merged_path)
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for e in validator.iter_errors(data):
        errors.append(f"{e.json_path}: {e.message}")
    return (len(errors) == 0, errors)


def validate_file_report(path: Path) -> str:
    ok, errs = validate_lesson_merged(path)
    if ok:
        return "OK"
    return "VALIDATION_FAILED:\n" + "\n".join(errs[:50])
