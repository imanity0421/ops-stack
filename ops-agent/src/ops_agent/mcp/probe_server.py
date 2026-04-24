"""
可选 MCP stdio 服务端：与 Agent 内 `fetch_ops_probe_context` 使用同一份 JSON。

运行：pip install -e ".[mcp]" 后
  python -m ops_agent.mcp.probe_server

在 Cursor / MCP 客户端中配置 command 为当前解释器，args 为 `-m ops_agent.mcp.probe_server`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ops_agent.config import Settings
from ops_agent.mcp.fixture_probe import format_probe_for_agent, load_probe_data


def main(argv: list[str] | None = None) -> int:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print('需要安装 MCP SDK：pip install -e ".[mcp]"', file=sys.stderr)
        return 1

    p = argparse.ArgumentParser(description="ops-agent MCP 探针（stdio）")
    p.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="覆盖默认探针 JSON（默认取 OPS_MCP_PROBE_FIXTURE_PATH 或包内资源）",
    )
    args = p.parse_args(argv)

    settings = Settings.from_env()
    path = args.fixture or settings.mcp_probe_fixture_path

    mcp = FastMCP("ops-agent-probe")

    @mcp.tool()
    def get_ops_probe_snapshot() -> str:
        """返回运营侧「市场/合规」探针摘要（fixture 或自定义 JSON）。"""
        data = load_probe_data(path)
        return format_probe_for_agent(data)

    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
