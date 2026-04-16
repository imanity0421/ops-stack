from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ops_knowledge.distill_stub import write_distill_stub
from ops_knowledge.manifest import build_manifest
from ops_knowledge.validate_merged import validate_lesson_merged


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ops-knowledge", description="校验 ① 产出并生成 handoff 清单")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="校验单个 lesson_merged.json")
    pv.add_argument("merged_json", type=Path, help="lesson_merged.json 路径")
    pv.add_argument("--schema", type=Path, default=None, help="覆盖 schema 路径")

    pm = sub.add_parser("manifest", help="扫描目录生成 handbook_handoff.json")
    pm.add_argument("--ingest-root", type=Path, required=True, help="① 输出根目录（递归查找 lesson_merged.json）")
    pm.add_argument("--output", "-o", type=Path, required=True, help="输出 JSON 路径")
    pm.add_argument("--schema", type=Path, default=None, help="覆盖 schema 路径")

    pd = sub.add_parser(
        "dspy-stub",
        help="无 LLM：从 lesson_merged.json 生成占位蒸馏 JSON（联调 DSPy 管线前使用）",
    )
    pd.add_argument("merged_json", type=Path, help="lesson_merged.json 路径")
    pd.add_argument("--output", "-o", type=Path, required=True, help="输出 JSON 路径")

    args = p.parse_args(argv)

    if args.cmd == "validate":
        ok, errs = validate_lesson_merged(args.merged_json, schema_path=args.schema)
        if ok:
            print("OK")
            return 0
        print("VALIDATION_FAILED", file=sys.stderr)
        for e in errs:
            print(e, file=sys.stderr)
        return 1

    if args.cmd == "manifest":
        manifest = build_manifest(args.ingest_root, schema_path=args.schema)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(manifest.to_json(), encoding="utf-8")
        print(f"Wrote {args.output} ({len(manifest.lessons)} lesson_merged.json)")
        invalid = sum(1 for x in manifest.lessons if not x.valid)
        if invalid:
            print(f"Warning: {invalid} files failed schema validation", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "dspy-stub":
        write_distill_stub(args.merged_json, args.output)
        print(f"Wrote stub distill -> {args.output}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
