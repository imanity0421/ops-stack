from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_SKILL_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class Settings:
    """从环境变量读取配置；未设置项使用安全默认值。"""

    openai_api_key: str | None = None
    openai_api_base: str | None = None
    mem0_api_key: str | None = None
    mem0_host: str | None = None
    snapshot_every_n_turns: int = 5
    local_memory_path: Path = Path("data/local_memory.json")
    hindsight_path: Path = Path("data/hindsight.jsonl")
    knowledge_fallback_path: Path | None = None
    handoff_manifest_path: Path | None = None
    golden_rules_path: Path | None = None
    mcp_probe_fixture_path: Path | None = None
    #: 可选；若设置则扫描其中 ``*.json`` 覆盖/增补内置 skill 配方（见 ``manifest_loader``）。
    agent_manifest_dir: Path | None = None
    #: 未显式传 ``skill_id`` 时使用的默认 skill（须存在于注册表，通常为 ``default_ops``）。
    default_skill_id: str = "default_ops"

    @classmethod
    def from_env(cls) -> Settings:
        fb = os.getenv("OPS_KNOWLEDGE_FALLBACK_PATH")
        ho = os.getenv("OPS_HANDOFF_MANIFEST_PATH")
        gr = os.getenv("OPS_GOLDEN_RULES_PATH")
        mp = os.getenv("OPS_MCP_PROBE_FIXTURE_PATH")
        am_dir = os.getenv("OPS_AGENT_MANIFEST_DIR")
        raw_skill = (os.getenv("OPS_AGENT_DEFAULT_SKILL_ID") or "default_ops").strip()
        default_skill_id = raw_skill if _SKILL_ID.match(raw_skill) else "default_ops"

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_api_base=os.getenv("OPENAI_API_BASE"),
            mem0_api_key=os.getenv("MEM0_API_KEY"),
            mem0_host=os.getenv("MEM0_HOST"),
            snapshot_every_n_turns=int(os.getenv("OPS_SNAPSHOT_EVERY_N_TURNS", "5")),
            local_memory_path=Path(os.getenv("OPS_LOCAL_MEMORY_PATH", "data/local_memory.json")),
            hindsight_path=Path(os.getenv("OPS_HISTORICAL_PATH", os.getenv("OPS_HISTORICAL_STUB_PATH", "data/hindsight.jsonl"))),
            knowledge_fallback_path=Path(fb) if fb else None,
            handoff_manifest_path=Path(ho) if ho else None,
            golden_rules_path=Path(gr) if gr else None,
            mcp_probe_fixture_path=Path(mp) if mp else None,
            agent_manifest_dir=Path(am_dir) if am_dir else None,
            default_skill_id=default_skill_id,
        )


def mem0_configured() -> bool:
    return bool(Settings.from_env().mem0_api_key)
