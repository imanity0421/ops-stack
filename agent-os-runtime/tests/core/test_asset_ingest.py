from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from agent_os.knowledge import asset_ingest as asset_ingest_mod
from agent_os.knowledge.asset_ingest import IngestOptions, ingest_jsonl, ingest_text
from agent_os.knowledge.asset_store import AssetCase


class DummyAssetStore:
    def __init__(self) -> None:
        self.cases: list[AssetCase] = []

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def upsert_many(self, cases: Sequence[AssetCase]) -> dict[str, Any]:
        self.cases.extend(cases)
        return {"status": "ok", "count": len(cases)}

    def find_case_id_by_dedup_key(self, dedup_key: str) -> str | None:
        _ = dedup_key
        return None

    def find_near_duplicate_case_id(self, *args: Any, **kwargs: Any) -> str | None:
        return None

    def delete_by_case_id(self, case_id: str) -> dict[str, Any]:
        return {"status": "ok", "case_id": case_id}

    def delete_by_client_skill(self, client_id: str, skill_id: str) -> dict[str, Any]:
        return {"status": "ok", "client_id": client_id, "skill_id": skill_id}


def test_ingest_jsonl_skips_non_object_rows(tmp_path: Path) -> None:
    p = tmp_path / "assets.jsonl"
    valid_text = "\n".join(
        f"行{i:03d} 通用案例包含目标、约束、步骤和复盘，用于验证非对象行不会中断批量导入。"
        for i in range(20)
    )
    p.write_text(
        '["not", "object"]\n42\n' + json.dumps({"text": valid_text}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    r = ingest_jsonl(
        p,
        store=DummyAssetStore(),
        opt=IngestOptions(
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
            allow_llm=False,
        ),
    )

    assert r["rejected"] == 2
    assert r["accepted"] == 1
    assert r["reasons"]["invalid_record"] == 2


def test_ingest_jsonl_accepts_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "assets.jsonl"
    valid_text = "\n".join(
        f"行{i:03d} 通用案例包含目标、约束、步骤和复盘，用于验证 BOM 文件不会中断批量导入。"
        for i in range(20)
    )
    p.write_text(
        "\ufeff" + json.dumps({"text": valid_text}, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    r = ingest_jsonl(
        p,
        store=DummyAssetStore(),
        opt=IngestOptions(
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
            allow_llm=False,
        ),
    )

    assert r["accepted"] == 1


def test_ingest_jsonl_bad_utf8_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "assets.jsonl"
    p.write_bytes(b"\xff\xfe\x00")

    r = ingest_jsonl(
        p,
        store=DummyAssetStore(),
        opt=IngestOptions(
            client_id="c1",
            user_id=None,
            skill_id="default_agent",
            allow_llm=False,
        ),
    )

    assert r["status"] == "error"
    assert r["reason"] == "invalid_input_file"


def test_ingest_assigns_scope_and_asset_type_without_llm(tmp_path: Path) -> None:
    p = tmp_path / "assets.jsonl"
    valid_text = "\n".join(
        f"行{i:03d} 这是一段用户创业经历背景素材，包含供应链危机、资金压力和复盘细节。"
        for i in range(20)
    )
    p.write_text(json.dumps({"text": valid_text}, ensure_ascii=False) + "\n", encoding="utf-8")
    store = DummyAssetStore()

    r = ingest_jsonl(
        p,
        store=store,
        opt=IngestOptions(
            client_id="c1",
            user_id="u1",
            skill_id="default_agent",
            asset_type="source_material",
            allow_llm=False,
        ),
    )

    assert r["accepted"] == 1
    assert store.cases[0].scope == "user_private"
    assert store.cases[0].asset_type == "source_material"
    assert store.cases[0].owner_user_id == "u1"


def test_ingest_text_quarantines_when_gatekeeper_fails(monkeypatch) -> None:
    text = "\n".join(
        f"行{i:03d} 这是一段足够长的案例正文，用于验证 gatekeeper 异常不会中断资产入库。"
        for i in range(20)
    )
    store = DummyAssetStore()

    def boom(*args: Any, **kwargs: Any) -> tuple[str, str | None]:
        _ = (args, kwargs)
        raise RuntimeError("llm unavailable")

    def features(*args: Any, **kwargs: Any) -> dict[str, Any]:
        _ = (args, kwargs)
        return {
            "asset_type": "style_reference",
            "primary_skill_hint": "default_agent",
            "applicable_skill_ids": ["default_agent"],
            "skill_confidence": 0.8,
            "feature_summary": "gatekeeper failure fallback",
            "summary": "summary",
            "style_fingerprint": "style",
            "tags": [],
            "style_tags": [],
            "content_tags": [],
            "key_excerpts": [],
        }

    monkeypatch.setattr(asset_ingest_mod, "_llm_gatekeeper", boom)
    monkeypatch.setattr(asset_ingest_mod, "_llm_extract_features", features)

    r = ingest_text(
        text,
        store=store,
        opt=IngestOptions(client_id="c1", user_id=None, skill_id="default_agent"),
    )

    assert r["status"] == "ok"
    assert r["case"]["status"] == "quarantined"
    assert r["case"]["reason"] == "gatekeeper_failed"
