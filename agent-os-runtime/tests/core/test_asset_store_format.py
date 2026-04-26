from __future__ import annotations

from agent_os.knowledge import asset_store as asset_store_mod
from agent_os.knowledge.asset_store import (
    AssetSearchHit,
    LanceDbAssetStore,
    _row_in_scope,
    _scope_rank,
    format_hits_for_agent,
)


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


def test_format_hits_for_agent_labels_asset_type_and_scope() -> None:
    text = format_hits_for_agent(
        [
            AssetSearchHit(
                case_id="case-2",
                scope="system",
                asset_type="style_reference",
                summary="金牌脚本摘要",
                feature_summary="痛点前置的脚本范例",
                style_fingerprint="强钩子、短句",
                style_tags=["痛点前置"],
                content_tags=["宝妈减脂"],
                primary_skill_hint="short_video",
            )
        ],
        include_raw=False,
    )
    assert "资产类型/范围：style_reference / system" in text
    assert "痛点前置的脚本范例" in text


def test_asset_scope_visibility_and_system_fallback_rank() -> None:
    private = {
        "status": "accepted",
        "scope": "user_private",
        "client_id": "c1",
        "owner_user_id": "u1",
        "skill_id": "s1",
    }
    shared = {
        "status": "accepted",
        "scope": "client_shared",
        "client_id": "c1",
        "skill_id": "s1",
    }
    system = {
        "status": "accepted",
        "scope": "system",
        "client_id": "system_global",
        "skill_id": "s1",
    }

    assert _row_in_scope(private, client_id="c1", user_id="u1", skill_id="s1")
    assert not _row_in_scope(private, client_id="c1", user_id="u2", skill_id="s1")
    assert _row_in_scope(shared, client_id="c1", user_id="u2", skill_id="s1")
    assert not _row_in_scope(shared, client_id="c2", user_id="u2", skill_id="s1")
    assert _row_in_scope(system, client_id="c2", user_id=None, skill_id="s1")
    assert _scope_rank(private, client_id="c1", user_id="u1") < _scope_rank(
        shared, client_id="c1", user_id="u1"
    )
    assert _scope_rank(shared, client_id="c1", user_id="u1") < _scope_rank(
        system, client_id="c1", user_id="u1"
    )


def test_lancedb_search_returns_empty_when_query_embedding_fails(tmp_path, monkeypatch) -> None:
    store = LanceDbAssetStore(path=tmp_path / "lance")
    monkeypatch.setattr(store, "_open_table", lambda: True)

    def boom(*args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("missing api key")

    monkeypatch.setattr(asset_store_mod, "_embed_text_openai", boom)

    assert (
        store.search(
            "query",
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
        )
        == []
    )
