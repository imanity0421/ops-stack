from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ops_distiller_forge import __version__
from ops_distiller_forge.config import ForgeSettings
from ops_distiller_forge.export.manifest import write_agent_manifest
from ops_distiller_forge.metrics.coverage import naive_recall_score
from ops_distiller_forge.ontology.models import AgentManifestV1, EpisodeBatchFile
from ops_distiller_forge.pipeline.episode_projector import knowledge_point_to_episode
from ops_distiller_forge.pipeline.map_stage import map_lesson_merged
from ops_distiller_forge.pipeline.reduce_stage import reduce_placeholder
from ops_distiller_forge.storage.jsonl_store import append_jsonl, read_jsonl
from ops_distiller_forge.storage.sqlite_store import SqliteKnowledgeStore


def _cmd_map(args: argparse.Namespace) -> int:
    settings = ForgeSettings.from_env()
    merged = Path(args.merged_json)
    kps = map_lesson_merged(
        merged,
        settings=settings,
        source_relpath=args.source_relpath,
        use_dspy=args.use_dspy,
    )
    out_jsonl = Path(args.out_jsonl)
    for kp in kps:
        append_jsonl(out_jsonl, kp)
    if args.sqlite:
        db = Path(args.sqlite)
        store = SqliteKnowledgeStore(db)
        for kp in kps:
            store.upsert(kp)
    print(f"wrote {len(kps)} knowledge point(s) -> {out_jsonl}")
    return 0


def _cmd_episodes(args: argparse.Namespace) -> int:
    path = Path(args.jsonl)
    episodes: list = []
    for row in read_jsonl(path):
        from ops_distiller_forge.ontology.models import KnowledgePoint

        kp = KnowledgePoint.model_validate(row)
        ep = knowledge_point_to_episode(kp, client_id=args.client_id)
        episodes.append(ep)
    batch = EpisodeBatchFile(client_id=args.client_id, episodes=episodes)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(batch.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(episodes)} episode(s) -> {out}")
    return 0


def _cmd_reduce(args: argparse.Namespace) -> int:
    from ops_distiller_forge.ontology.models import KnowledgePoint

    points: list[KnowledgePoint] = []
    for row in read_jsonl(Path(args.jsonl)):
        points.append(KnowledgePoint.model_validate(row))
    merged = reduce_placeholder(points)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for kp in merged:
            f.write(kp.model_dump_json(ensure_ascii=False) + "\n")
    print(f"reduce: {len(points)} -> {len(merged)} -> {out}")
    return 0


def _cmd_export_manifest(args: argparse.Namespace) -> int:
    default_tools = [
        "retrieve_ordered_context",
        "search_domain_knowledge",
        "fetch_ops_probe_context",
    ]
    tools = json.loads(args.enabled_tools_json) if args.enabled_tools_json else default_tools
    m = AgentManifestV1(
        handbook_version=args.handbook_version,
        system_prompt=args.system_prompt or "",
        model=args.model,
        temperature=args.temperature,
        enabled_tools=tools,
    )
    write_agent_manifest(Path(args.output), m)
    print(f"wrote manifest -> {args.output}")
    return 0


def _cmd_eval_recall(args: argparse.Namespace) -> int:
    text = Path(args.text_file).read_text(encoding="utf-8")
    gt = json.loads(Path(args.ground_truth_json).read_text(encoding="utf-8"))
    if not isinstance(gt, list):
        print("ground_truth_json must be a JSON array of strings", file=sys.stderr)
        return 1
    phrases = [str(x) for x in gt]
    score = naive_recall_score(text, phrases)
    print(json.dumps({"naive_recall": score, "phrases": len(phrases)}, ensure_ascii=False, indent=2))
    return 0 if score >= args.threshold else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ops-distiller", description="ops-distiller-forge ② 炼金工坊")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("map", help="lesson_merged.json -> KnowledgePoint JSONL（+ 可选 SQLite）")
    pm.add_argument("merged_json", type=Path)
    pm.add_argument("--out-jsonl", "-o", type=Path, required=True)
    pm.add_argument("--source-relpath", default=None, help="写入溯源的路径名")
    pm.add_argument("--sqlite", type=Path, default=None, help="可选 SQLite 路径")
    pm.add_argument("--use-dspy", action="store_true", help="使用 DSPy+LLM（需 pip install -e '.[dspy]' 与 OPENAI_API_KEY）")
    pm.set_defaults(func=_cmd_map)

    pe = sub.add_parser("episodes", help="KnowledgePoint JSONL -> graphiti-ingest 兼容 JSON")
    pe.add_argument("--jsonl", type=Path, required=True)
    pe.add_argument("--output", "-o", type=Path, required=True)
    pe.add_argument("--client-id", default="demo_client")
    pe.set_defaults(func=_cmd_episodes)

    pr = sub.add_parser("reduce", help="多课 JSONL 粗归并（占位策略）")
    pr.add_argument("--jsonl", type=Path, required=True)
    pr.add_argument("--output", "-o", type=Path, required=True)
    pr.set_defaults(func=_cmd_reduce)

    px = sub.add_parser("export-manifest", help="写入 agent_config 风格 Manifest（③ Loader 对接）")
    px.add_argument("--output", "-o", type=Path, required=True)
    px.add_argument("--handbook-version", default="0.1.0")
    px.add_argument("--system-prompt", default="", help="多行可换行符")
    px.add_argument("--model", default="gpt-4o-mini")
    px.add_argument("--temperature", type=float, default=0.2)
    px.add_argument("--enabled-tools-json", default=None, help='JSON 数组，如 ["retrieve_ordered_context"]')
    px.set_defaults(func=_cmd_export_manifest)

    ev = sub.add_parser("eval-recall", help="粗 recall：关键词是否在文本中（baseline）")
    ev.add_argument("--text-file", type=Path, required=True)
    ev.add_argument("--ground-truth-json", type=Path, required=True)
    ev.add_argument("--threshold", type=float, default=0.0)
    ev.set_defaults(func=_cmd_eval_recall)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
