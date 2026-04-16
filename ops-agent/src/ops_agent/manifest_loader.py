from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentManifestV1(BaseModel):
    """
    与 ops-distiller-forge 导出的 agent_config JSON 对齐（字段子集可扩展）。
    见 forge `AgentManifestV1`；此处重复定义以避免运行时依赖 forge。
    """

    manifest_version: str = "1.0"
    handbook_version: str = "0.1.0"
    system_prompt: str = ""
    model: str | None = "gpt-4o-mini"
    temperature: float | None = None
    enabled_tools: list[str] = Field(default_factory=list)
    notes: str | None = None


def load_agent_manifest(path: Path | None) -> AgentManifestV1 | None:
    if path is None or not path.is_file():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return AgentManifestV1.model_validate(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("无法加载 OPS_AGENT_MANIFEST_PATH: %s", e)
        return None


def enabled_tool_name_set(manifest: AgentManifestV1 | None) -> set[str] | None:
    """若 manifest 未设或列表为空，返回 None 表示「不筛选，使用全部工具」。"""
    if manifest is None:
        return None
    if not manifest.enabled_tools:
        return None
    return set(manifest.enabled_tools)
