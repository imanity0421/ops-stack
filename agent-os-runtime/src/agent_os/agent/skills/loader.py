"""
Skill 包动态加载：仅允许自 ``agent_os.agent.skills.<name>`` 子包且 name 在环境白名单内。

非白名单子包**永不 import**，防路径遍历与任意代码执行。详见 docs/AGENT_OS_ROADMAP.md。
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_os.config import Settings

logger = logging.getLogger(__name__)

# 子包名：仅允许标识符，禁止 "." 等路径注入
_SAFE_PKG = re.compile(r"^[a-zA-Z0-9_]+$")

_SKILLS_PREFIX = "agent_os.agent.skills"


class SkillPackageLoadError(RuntimeError):
    """白名单内技能包 import 后不符合契约（如缺少 get_tools）。"""


def get_incremental_tools_for_skill(
    skill_id: str,
    *,
    settings: "Settings | None" = None,
) -> list[Callable[..., object]]:
    """
    返回指定 ``skill_id`` 的额外 Agno 工具列表。

    - 白名单为空：不加载任何技能包，返回空列表（平台工具仍由 ``build_memory_tools`` 提供）。
    - ``skill_id`` 不在白名单：不 import，返回 []。
    - ``skill_id`` 在白名单：``importlib.import_module("agent_os.agent.skills.<skill_id>")``，调用 ``get_tools()``。
    """
    from agent_os.config import Settings

    s = settings or Settings.from_env()
    allow: frozenset[str] = s.skill_packages_allowlist
    if not allow or skill_id not in allow:
        return []

    if not _SAFE_PKG.match(skill_id):
        raise ValueError(f"skill_id 非法，拒绝加载: {skill_id!r}")

    modname = f"{_SKILLS_PREFIX}.{skill_id}"
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as e:
        raise SkillPackageLoadError(
            f"白名单内技能包未找到或无法加载: {modname}。请确认子包已安装。原始错误: {e}"
        ) from e

    get_tools = getattr(mod, "get_tools", None)
    if not callable(get_tools):
        raise SkillPackageLoadError(
            f"模块 {modname} 必须提供可调用对象 get_tools() -> list[Callable]"
        )
    try:
        tools = get_tools()
    except Exception as e:
        raise SkillPackageLoadError(f"{modname}.get_tools() 执行失败: {e}") from e
    if not isinstance(tools, list):
        raise SkillPackageLoadError(f"{modname}.get_tools() 须返回 list，实际: {type(tools)}")

    logger.info("Skill 包已加载: %s，工具数=%d", modname, len(tools))
    return tools
