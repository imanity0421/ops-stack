from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


def _builtin_probe_path() -> Path:
    """开发模式下用于测试的资源路径（与包内资源等价）。"""
    return Path(__file__).resolve().parents[1] / "resources" / "mcp_probe_default.json"


def load_probe_data(fixture_path: Path | None) -> dict[str, Any]:
    """
    加载探针 JSON：优先 OPS_MCP_PROBE_FIXTURE_PATH / 参数路径；
    否则使用包内 `resources/mcp_probe_default.json`。
    """
    if fixture_path is not None and fixture_path.is_file():
        raw = fixture_path.read_text(encoding="utf-8")
        return json.loads(raw)
    try:
        txt = resources.files("ops_agent.resources").joinpath("mcp_probe_default.json").read_text(encoding="utf-8")
        return json.loads(txt)
    except (FileNotFoundError, ModuleNotFoundError, OSError, KeyError):
        p = _builtin_probe_path()
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return {"error": "no_probe_fixture", "hint": "设置 OPS_MCP_PROBE_FIXTURE_PATH 或检查包内资源"}


def format_probe_for_agent(data: dict[str, Any]) -> str:
    """压缩为模型可读短文本。"""
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)
    snap = data.get("market_snapshot") or {}
    lines = [
        "[Ops 探针 / fixture]",
        f"版本: {data.get('probe_version', '?')}",
        f"平台: {snap.get('platform', '')}",
        f"类目: {snap.get('category', '')}",
        f"参考 CTR 区间: {snap.get('benchmark_ctr_range', '')}",
    ]
    risks = data.get("risk_flags")
    if isinstance(risks, list) and risks:
        lines.append("风险提示: " + "；".join(str(x) for x in risks[:8]))
    note = snap.get("note")
    if note:
        lines.append(f"说明: {note}")
    return "\n".join(lines)
