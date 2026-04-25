from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

_SKILL_STEM = re.compile(r"^[a-zA-Z0-9_-]+$")


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
    agent_name: str | None = None
    #: P1-3 可选；追加在系统宪法核心段**之后**、``system_prompt`` 之前
    constitutional_prompt: str | None = None
    #: P1-4；为 ``structured_v1`` 时由 ``manifest_output.resolve_structured_output_model`` 解析为 Agno ``output_schema``
    output_mode: str | None = None
    output_schema_version: str | None = None


def packaged_skill_manifest_dir() -> Path:
    """内置 skill 配方目录（随包分发）：``{skill_id}.json``。"""
    return Path(__file__).resolve().parent / "data" / "skill_manifests"


def load_agent_manifest(path: Path | None) -> AgentManifestV1 | None:
    """加载单个 manifest 文件（单测、工具脚本复用）。"""
    if path is None or not path.is_file():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8-sig"))
        return AgentManifestV1.model_validate(raw)
    except (json.JSONDecodeError, OSError, ValidationError) as e:
        logger.warning("无法加载 manifest 文件 %s: %s", path, e)
        return None


def _absorb_manifest_dir(target: dict[str, AgentManifestV1], directory: Path) -> None:
    if not directory.is_dir():
        logger.warning("Skill manifest 目录不存在或不是目录: %s", directory)
        return
    for path in sorted(directory.glob("*.json")):
        stem = path.stem
        if not _SKILL_STEM.match(stem):
            logger.warning("跳过非法 skill 文件名（须 ^[a-zA-Z0-9_-]+$）: %s", path.name)
            continue
        m = load_agent_manifest(path)
        if m is not None:
            target[stem] = m


def resolve_effective_skill_id(
    requested: str | None,
    default_id: str,
    registry: dict[str, AgentManifestV1],
) -> str:
    """将调用方传入的 skill（可为 None）解析为注册表中存在的键。"""
    sid = (requested if requested is not None else default_id).strip()
    if sid in registry:
        return sid
    logger.warning("未知 skill_id=%s，回退为 %s", sid, default_id)
    if default_id in registry:
        return default_id
    if registry:
        return sorted(registry.keys())[0]
    return default_id


def load_skill_manifest_registry(overlay_dir: Path | None = None) -> dict[str, AgentManifestV1]:
    """
    Skill 注册表：先加载包内置目录，再若设置 ``overlay_dir`` 则合并扫描（同名 skill 由覆盖层覆盖）。

    - 文件名 ``{skill_id}.json`` → 注册键 ``skill_id``。
    - 未设置 ``AGENT_OS_MANIFEST_DIR`` 时仍可使用内置 ``default_agent``。
    """
    reg: dict[str, AgentManifestV1] = {}
    _absorb_manifest_dir(reg, packaged_skill_manifest_dir())
    if overlay_dir is not None:
        _absorb_manifest_dir(reg, overlay_dir)
    return reg


def enabled_tool_name_set(manifest: AgentManifestV1 | None) -> set[str] | None:
    """若 manifest 未设或列表为空，返回 None 表示「不筛选，使用全部工具」。"""
    if manifest is None:
        return None
    if not manifest.enabled_tools:
        return None
    return set(manifest.enabled_tools)
