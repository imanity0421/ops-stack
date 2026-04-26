from __future__ import annotations

import json
import os
import sys
import importlib.util
from pathlib import Path
from typing import Any

from agent_os.config import Settings
from agent_os.knowledge.graphiti_entitlements import validate_entitlements_document
from agent_os.manifest_loader import load_skill_manifest_registry


def _ok(msg: str) -> None:
    print(f"[ok] {msg}")


def _warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def _fail(msg: str) -> None:
    print(f"[fail] {msg}", file=sys.stderr)


def run_doctor(*, strict: bool = False) -> int:
    """
    环境自检。strict=True 时：缺少 OPENAI_API_KEY 返回非零。
    """
    exit_code = 0
    try:
        s = Settings.from_env()
    except ValueError as e:
        _fail(f"配置环境变量无效: {e}")
        return 1

    if sys.version_info < (3, 10):
        _fail("需要 Python 3.10+")
        return 1

    if s.openai_api_key:
        _ok("OPENAI_API_KEY 已设置")
    else:
        _fail("未设置 OPENAI_API_KEY（对话与 AsyncReview 必需）")
        if strict:
            exit_code = 1
        else:
            _warn("非 strict：仍返回 0，仅作提示")

    if s.openai_api_base:
        _ok(f"OPENAI_API_BASE={s.openai_api_base}")

    if s.mem0_api_key:
        _ok("MEM0_API_KEY 已设置（将使用托管 Mem0）")
    else:
        _warn("未设置 MEM0_API_KEY，使用本地 JSON：" + str(s.local_memory_path))

    if os.getenv("NEO4J_URI") and os.getenv("NEO4J_PASSWORD"):
        _ok("NEO4J_URI / NEO4J_PASSWORD 已设置（Graphiti 可用）")
        try:
            import graphiti_core  # noqa: F401

            _ok("graphiti-core 已安装")
        except ImportError:
            _warn('未安装 graphiti-core，请 pip install -e ".[graphiti]"')
    else:
        _warn("未配置 Neo4j，Graphiti 检索将走 JSONL 或提示未配置")

    ent_path = Path(
        os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", "data/graphiti_entitlements.json")
    )
    ent_store = (os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_STORE") or "file").strip().lower()
    if ent_store != "file":
        _warn(f"AGENT_OS_GRAPHITI_ENTITLEMENTS_STORE={ent_store!r} 已降级；当前仅支持 file 后端")
    if ent_path.is_file():
        try:
            ent_obj: Any = json.loads(ent_path.read_text(encoding="utf-8-sig"))
            errs = validate_entitlements_document(ent_obj)
            if errs:
                _warn("Graphiti 权限文件结构不合法: " + "; ".join(errs) + f"（path={ent_path}）")
            else:
                _ok(f"Graphiti 权限文件结构合法: {ent_path}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            _warn(f"Graphiti 权限文件 JSON 无效: {ent_path} ({e})")
    else:
        _warn(f"Graphiti 权限文件不存在（将回退 env 授权）: {ent_path}")

    if s.knowledge_fallback_path:
        if s.knowledge_fallback_path.is_file():
            _ok(f"AGENT_OS_KNOWLEDGE_FALLBACK_PATH 存在: {s.knowledge_fallback_path}")
        else:
            _warn(f"AGENT_OS_KNOWLEDGE_FALLBACK_PATH 指向的文件不存在: {s.knowledge_fallback_path}")

    handoff = os.getenv("AGENT_OS_HANDOFF_MANIFEST_PATH")
    if handoff:
        p = Path(handoff)
        if p.is_file():
            try:
                data: Any = json.loads(p.read_text(encoding="utf-8-sig"))
                lessons = data.get("lessons") if isinstance(data, dict) else None
                n = len(lessons) if isinstance(lessons, list) else "?"
                _ok(f"AGENT_OS_HANDOFF_MANIFEST_PATH 可读: {p} (lessons≈{n})")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                _warn(f"handoff 清单 JSON 无效: {e}")
        else:
            _warn(f"AGENT_OS_HANDOFF_MANIFEST_PATH 不存在: {p}")

    am_dir = os.getenv("AGENT_OS_MANIFEST_DIR")
    if am_dir:
        dp = Path(am_dir)
        if dp.is_dir():
            reg = load_skill_manifest_registry(dp)
            _ok(f"AGENT_OS_MANIFEST_DIR 可扫描: {dp}（skill 数: {len(reg)}）")
        else:
            _warn(f"AGENT_OS_MANIFEST_DIR 不是目录: {dp}")
    else:
        reg = load_skill_manifest_registry(None)
        _ok(f"未设置 AGENT_OS_MANIFEST_DIR，使用内置 skill 配方（skill 数: {len(reg)}）")

    mp = os.getenv("AGENT_OS_MCP_PROBE_FIXTURE_PATH")
    if mp:
        pp = Path(mp)
        if pp.is_file():
            _ok(f"AGENT_OS_MCP_PROBE_FIXTURE_PATH 存在: {pp}")
        else:
            _warn(f"AGENT_OS_MCP_PROBE_FIXTURE_PATH 指向的文件不存在: {pp}")

    if s.enable_asset_store:
        if importlib.util.find_spec("lancedb") is None:
            _warn('已启用 Asset Store，但未安装 lancedb；请 pip install -e ".[asset_store]"')
            if strict:
                exit_code = 1
        else:
            _ok("lancedb 已安装（Asset Store 可用）")

    web_admin_on = os.getenv("AGENT_OS_WEB_ENABLE_ADMIN_API", "0").lower() in ("1", "true", "yes")
    if web_admin_on:
        tok = (
            os.getenv("AGENT_OS_WEB_ADMIN_API_TOKENS")
            or os.getenv("AGENT_OS_WEB_ADMIN_API_TOKEN")
            or ""
        ).strip()
        if tok:
            _ok("Web 管理接口 token 已配置（AGENT_OS_WEB_ADMIN_API_TOKEN(S)）")
        else:
            _warn("已启用 AGENT_OS_WEB_ENABLE_ADMIN_API，但未配置 AGENT_OS_WEB_ADMIN_API_TOKEN(S)")

    gr = os.getenv("AGENT_OS_GOLDEN_RULES_PATH")
    if gr:
        gp = Path(gr)
        if gp.is_file():
            _ok(f"AGENT_OS_GOLDEN_RULES_PATH 存在: {gp}")
        else:
            _warn(f"AGENT_OS_GOLDEN_RULES_PATH 指向的文件不存在: {gp}")

    vr = os.getenv("VIDEO_RAW_INGEST_ROOT")
    if vr:
        schema = Path(vr) / "schema" / "lesson_merged.schema.json"
        if schema.is_file():
            _ok(f"VIDEO_RAW_INGEST_ROOT 下存在 schema: {schema}")
        else:
            _warn(f"VIDEO_RAW_INGEST_ROOT 未找到 schema: {schema}")

    return exit_code
