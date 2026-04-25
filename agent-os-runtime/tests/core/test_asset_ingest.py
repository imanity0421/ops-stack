from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from agent_os.knowledge.asset_ingest import IngestOptions, ingest_jsonl
from agent_os.knowledge.asset_store import AssetCase


class DummyAssetStore:
    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def upsert_many(self, cases: Sequence[AssetCase]) -> dict[str, Any]:
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
