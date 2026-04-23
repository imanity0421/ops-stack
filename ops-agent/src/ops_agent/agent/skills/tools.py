from __future__ import annotations

from collections.abc import Callable


def get_incremental_tools(skill_id: str) -> list[Callable[..., object]]:
    """
    各 skill 专属、非平台层的额外 Agno 工具。

    Phase 1 占位：当前无增量工具；后续可在本模块按 ``skill_id`` 分支注册。
    """
    _ = skill_id
    return []
