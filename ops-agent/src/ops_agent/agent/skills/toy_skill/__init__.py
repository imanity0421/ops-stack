"""
测试/示例技能包：用于验证 Skill 白名单动态加载（Sprint 1 P0-1）。

需在环境 ``OPS_AGENT_LOADABLE_SKILL_PACKAGES`` 中包含 ``toy_skill`` 才会加载。
"""

from __future__ import annotations

from collections.abc import Callable

from agno.tools import tool


@tool(
    name="ping_toy_skill",
    description="toy_skill 技能包连通性测试：返回固定字符串，不访问外网。",
)
def ping_toy_skill() -> str:
    return "pong:toy_skill"


def get_tools() -> list[Callable[..., object]]:
    """技能包入口：由 ``ops_agent.agent.skills.loader`` 调用。"""
    return [ping_toy_skill]
