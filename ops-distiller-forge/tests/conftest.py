from pathlib import Path

import pytest


@pytest.fixture
def minimal_merged(tmp_path: Path) -> Path:
    data = {
        "schema_version": "1.0",
        "video": {"path": "x.mp4"},
        "speech": {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "私域运营要先做信任再做转化。"},
                {"start": 2.0, "end": 4.0, "text": "第二步设计低价钩子产品。"},
            ],
            "empty": False,
        },
        "visual": {"slides": [], "empty": True},
        "merged": {
            "timeline": [
                {"kind": "speech", "start_sec": 0.0, "end_sec": 2.0, "text": "私域运营要先做信任再做转化。"},
            ]
        },
    }
    import json

    p = tmp_path / "lesson_merged.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p
