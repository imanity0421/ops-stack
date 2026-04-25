from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_os.agent.skills.loader import get_incremental_tools_for_skill

if TYPE_CHECKING:
    from agent_os.config import Settings


def get_incremental_tools(
    skill_id: str,
    *,
    settings: "Settings | None" = None,
) -> list[Callable[..., object]]:
    """
    各 skill 专属、非平台层的额外 Agno 工具。

    自包内子模块 ``agent_os.agent.skills.<skill_id>`` 按白名单动态加载，见
    ``AGENT_OS_LOADABLE_SKILL_PACKAGES`` 与 :func:`get_incremental_tools_for_skill`。
    """
    return get_incremental_tools_for_skill(skill_id, settings=settings)
