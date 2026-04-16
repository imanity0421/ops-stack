from __future__ import annotations

import json

from ops_knowledge.distill_stub import distill_stub_from_merged


def test_distill_stub(minimal_merged) -> None:
    out = distill_stub_from_merged(minimal_merged)
    assert out.get("stub") is True
    assert "speech_preview" in out
    assert out.get("schema_version") == "1.0"
