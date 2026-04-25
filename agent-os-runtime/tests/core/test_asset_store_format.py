from __future__ import annotations

from agent_os.knowledge.asset_store import AssetSearchHit, format_hits_for_agent


def test_format_hits_for_agent_includes_created_at() -> None:
    text = format_hits_for_agent(
        [
            AssetSearchHit(
                case_id="case-1",
                summary="成功案例摘要",
                style_fingerprint="克制、清晰",
                tags=["通用案例"],
                created_at="2026-04-25T10:00:00+00:00",
            )
        ],
        include_raw=False,
    )
    assert "记录于：2026-04-25T10:00:00+00:00" in text
