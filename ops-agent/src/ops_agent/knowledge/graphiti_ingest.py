from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops_agent.knowledge.group_id import sanitize_group_id

logger = logging.getLogger(__name__)


async def ingest_episodes_file(
    episodes_json: Path,
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
) -> list[str]:
    """
    离线写入 Graphiti：每条约一条 episode（需 OpenAI 等 LLM 做实体抽取）。

    JSON 格式示例见 `docs/examples/graphiti_episodes.example.json`。
    """
    from graphiti_core.graphiti import Graphiti
    from graphiti_core.nodes import EpisodeType

    raw = json.loads(episodes_json.read_text(encoding="utf-8"))
    default_client = "demo_client"
    items: list[dict[str, Any]]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and "episodes" in raw:
        items = list(raw["episodes"])
        default_client = str(raw.get("client_id") or default_client)
    else:
        raise ValueError("JSON 须为数组或含 episodes 数组的对象")

    g = Graphiti(uri=neo4j_uri, user=neo4j_user, password=neo4j_password)
    uuids: list[str] = []
    for i, ep in enumerate(items):
        name = str(ep.get("name", f"episode_{i}"))
        body = str(ep.get("body", ep.get("text", "")))
        if not body.strip():
            logger.warning("跳过空 body: %s", name)
            continue
        desc = str(ep.get("source_description", "offline_ingest"))
        cid = str(ep.get("client_id") or default_client)
        group_id = ep.get("group_id") or sanitize_group_id(cid)
        ref = ep.get("reference_time_utc")
        if ref:
            reference_time = datetime.fromisoformat(str(ref).replace("Z", "+00:00"))
        else:
            reference_time = datetime.now(timezone.utc)
        src_raw = ep.get("source", "text")
        src = EpisodeType.text if str(src_raw).lower() in ("text", "lesson") else EpisodeType.message

        kwargs = {
            "name": name,
            "episode_body": body,
            "source_description": desc,
            "reference_time": reference_time,
            "source": src,
            "group_id": group_id,
        }
        r = await g.add_episode(**kwargs)
        uuids.append(str(r.episode.uuid))
    return uuids


def run_ingest_sync(episodes_json: Path, *, neo4j_uri: str, neo4j_user: str, neo4j_password: str) -> list[str]:
    return asyncio.run(ingest_episodes_file(episodes_json, neo4j_uri=neo4j_uri, neo4j_user=neo4j_user, neo4j_password=neo4j_password))
