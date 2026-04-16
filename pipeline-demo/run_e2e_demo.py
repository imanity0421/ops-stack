#!/usr/bin/env python3
"""
端到端演示：① 模拟 merged → ②a validate/manifest + ②b map/episodes/manifest → ③ 环境变量说明。

依赖：已在各子目录 pip install -e ops-knowledge、ops-distiller-forge（及可选 ops-agent）。
子目录名以 ``ops-stack/ops-stack.toml`` 为准（勿在脚本里写死文件夹名）。

用法（在 ops-stack 根目录下）:
  python pipeline-demo/run_e2e_demo.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _py() -> str:
    return sys.executable


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def _load_layout_mod(ops_stack_root: Path):
    spec = importlib.util.spec_from_file_location(
        "ops_stack_load_layout",
        ops_stack_root / "load_layout.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {ops_stack_root / 'load_layout.py'}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="①②③ 模拟数据串联演示")
    ap.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="lesson_merged.schema.json（默认：ops-knowledge/tests/fixtures）",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    coding = root.parent
    layout = _load_layout_mod(coding)
    repos = layout.load_repos(coding)

    out = root / "out"
    batch = out / "batch_demo"
    lesson_dir = batch / "lesson1"
    out.mkdir(exist_ok=True)
    batch.mkdir(parents=True, exist_ok=True)
    lesson_dir.mkdir(parents=True, exist_ok=True)

    merged_src = root / "fixtures" / "lesson_merged.json"
    merged = lesson_dir / "lesson_merged.json"
    shutil.copyfile(merged_src, merged)

    schema = args.schema
    if schema is None:
        schema = coding / repos["ops_knowledge"] / "tests" / "fixtures" / "lesson_merged.schema.json"
    if not schema.is_file():
        print(f"ERROR: schema 不存在: {schema}", file=sys.stderr)
        return 1

    okp = coding / repos["ops_knowledge"]
    forge = coding / repos["ops_distiller_forge"]
    agent_dir = coding / repos["ops_agent"]
    if not (okp / "pyproject.toml").is_file() or not (forge / "pyproject.toml").is_file():
        print(
            "ERROR: 请在 ops-stack 根目录下运行（需存在 ops-knowledge 与 ops-distiller-forge，"
            "路径见 ops-stack.toml）",
            file=sys.stderr,
        )
        return 1

    # ①②a：校验 + handoff 清单
    _run(
        [
            _py(),
            "-m",
            "ops_knowledge",
            "validate",
            str(merged),
            "--schema",
            str(schema),
        ],
        cwd=okp,
    )
    handoff = out / "handbook_handoff.json"
    _run(
        [
            _py(),
            "-m",
            "ops_knowledge",
            "manifest",
            "--ingest-root",
            str(batch),
            "-o",
            str(handoff),
            "--schema",
            str(schema),
        ],
        cwd=okp,
    )

    kp_jsonl = out / "knowledge_points.jsonl"
    episodes_json = out / "episodes.json"
    agent_cfg = out / "agent_config.json"
    system_prompt = (root / "fixtures" / "agent_system_prompt.txt").read_text(encoding="utf-8").strip()

    # ②b：map / episodes / export-manifest
    _run(
        [
            _py(),
            "-m",
            "ops_distiller_forge",
            "map",
            str(merged),
            "-o",
            str(kp_jsonl),
            "--source-relpath",
            "lesson1/lesson_merged.json",
        ],
        cwd=forge,
    )
    _run(
        [
            _py(),
            "-m",
            "ops_distiller_forge",
            "episodes",
            "--jsonl",
            str(kp_jsonl),
            "-o",
            str(episodes_json),
            "--client-id",
            "demo_client",
        ],
        cwd=forge,
    )
    _run(
        [
            _py(),
            "-m",
            "ops_distiller_forge",
            "export-manifest",
            "-o",
            str(agent_cfg),
            "--handbook-version",
            "demo-0.1.0",
            "--system-prompt",
            system_prompt,
        ],
        cwd=forge,
    )

    # ③：降级 JSONL（从 episode 正文抽一条，便于无 Neo4j 时检索）
    fallback = out / "knowledge_fallback.jsonl"
    ep_data = json.loads(episodes_json.read_text(encoding="utf-8"))
    first_body = ""
    for ep in ep_data.get("episodes", []):
        first_body = str(ep.get("body", ""))[:2000]
        if first_body.strip():
            break
    line = json.dumps(
        {"group_id": "demo_client", "text": first_body or "私域运营演示知识点"},
        ensure_ascii=False,
    )
    fallback.write_text(line + "\n", encoding="utf-8")

    env_lines = [
        "# 将下列变量写入 ops-agent 运行环境（PowerShell 示例）",
        f'$env:OPS_HANDOFF_MANIFEST_PATH = "{handoff.resolve()}"',
        f'$env:OPS_AGENT_MANIFEST_PATH = "{agent_cfg.resolve()}"',
        f'$env:OPS_KNOWLEDGE_FALLBACK_PATH = "{fallback.resolve()}"',
        "",
        "# 然后（需 OPENAI_API_KEY）",
        f'# cd "{agent_dir.resolve()}" && python -m ops_agent --client-id demo_client --no-knowledge',
        "# 若已配置 Neo4j，可去掉 --no-knowledge，并可用下列命令入库 Graphiti（可选）",
        f'# cd "{agent_dir.resolve()}" && ops-agent graphiti-ingest "{episodes_json.resolve()}" --dry-run',
    ]
    env_file = out / "env_snippet.ps1"
    env_file.write_text("\n".join(env_lines), encoding="utf-8")

    print("", flush=True)
    print("OK. 产物目录:", out.resolve(), flush=True)
    print("环境变量片段:", env_file.resolve(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
