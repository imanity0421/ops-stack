from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from agent_os.agent.factory import get_agent, new_session_id
from agent_os.config import Settings
from agent_os.knowledge.asset_store import asset_store_from_settings
from agent_os.knowledge.graphiti_reader import GraphitiReadService
from agent_os.memory.controller import MemoryController
from agent_os.review.async_review import AsyncReviewService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("agent_os.cli")


def _doctor_main(argv: list[str]) -> int:
    from agent_os.doctor import run_doctor

    p = argparse.ArgumentParser(prog="agent-os-runtime doctor", description="环境与依赖自检")
    p.add_argument("--strict", action="store_true", help="缺少 OPENAI_API_KEY 时返回非零")
    args = p.parse_args(argv)
    return run_doctor(strict=args.strict)


def _eval_main(argv: list[str]) -> int:
    from agent_os.evaluator.e2e import run_e2e_eval_file

    p = argparse.ArgumentParser(
        prog="agent-os-runtime eval", description="端到端规则评测（Golden rules，无 LLM）"
    )
    p.add_argument(
        "case_file",
        type=Path,
        help="JSON：name / assistant_turns / golden_rules 或 golden_rules_path",
    )
    args = p.parse_args(argv)
    r = run_e2e_eval_file(args.case_file)
    print(
        json.dumps(
            {
                "name": r.name,
                "passed": r.passed,
                "assistant_turns_checked": r.assistant_turns_checked,
                "violations": r.violations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if r.passed else 1


def _knowledge_append_main(argv: list[str]) -> int:
    from agent_os.knowledge.jsonl_append import append_knowledge_lines

    p = argparse.ArgumentParser(
        prog="agent-os-runtime knowledge-append-jsonl",
        description="向 AGENT_OS_KNOWLEDGE_FALLBACK_PATH 格式 JSONL 追加领域知识行（无需 Neo4j）",
    )
    p.add_argument("--output", "-o", type=Path, required=True, help="JSONL 路径")
    p.add_argument(
        "--client-id", required=True, help="租户 ID（与 skill 共同映射为 Graphiti group_id）"
    )
    p.add_argument(
        "--skill",
        default="default_agent",
        help="skill_id，与运行时 AGENT_OS_DEFAULT_SKILL_ID 一致；写入 graphiti_group_id(client, skill)",
    )
    p.add_argument("--text", action="append", required=True, help="一条或多条文本（可重复）")
    args = p.parse_args(argv)
    n = append_knowledge_lines(args.output, args.client_id, args.text, skill_id=args.skill)
    print(f"appended {n} lines -> {args.output}")
    return 0


def _graphiti_ingest_main(argv: list[str]) -> int:
    from agent_os.knowledge.graphiti_ingest import run_ingest_sync

    p = argparse.ArgumentParser(
        prog="agent-os-runtime graphiti-ingest",
        description='离线 Graphiti add_episode（需 NEO4J_*、OPENAI_API_KEY、pip install -e ".[graphiti]"）',
    )
    p.add_argument(
        "episodes_json", type=Path, help="见 docs/examples/graphiti_episodes.example.json"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="仅校验 JSON 结构，不连接数据库",
    )
    args = p.parse_args(argv)
    if args.dry_run:
        import json as _json

        raw = _json.loads(args.episodes_json.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            _ = raw
        elif isinstance(raw, dict) and "episodes" in raw:
            _ = raw["episodes"]
        else:
            print("JSON 须为数组或含 episodes", file=sys.stderr)
            return 1
        print("dry-run OK")
        return 0

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER", "neo4j")
    pw = os.getenv("NEO4J_PASSWORD")
    if not uri or not pw:
        print("需要环境变量 NEO4J_URI 与 NEO4J_PASSWORD", file=sys.stderr)
        return 1
    uuids = run_ingest_sync(args.episodes_json, neo4j_uri=uri, neo4j_user=user, neo4j_password=pw)
    print(json.dumps({"episode_uuids": uuids}, ensure_ascii=False, indent=2))
    return 0


def _asset_ingest_main(argv: list[str]) -> int:
    from agent_os.knowledge.asset_ingest import IngestOptions, ingest_jsonl, ingest_text

    p = argparse.ArgumentParser(
        prog="agent-os-runtime asset-ingest",
        description="离线导入参考案例库（Asset Store / LanceDB）。运行时不清洗，清洗/特征抽取在此阶段完成。",
    )
    p.add_argument("input", type=Path, help="输入：.jsonl（每行一个案例）或 .txt（单案例纯文本）")
    p.add_argument("--client-id", required=True, help="租户 ID")
    p.add_argument("--user-id", default=None, help="终端用户 ID（可选；为空表示租户共享）")
    p.add_argument("--skill", default="default_agent", help="skill_id（默认 default_agent）")
    p.add_argument("--source", default=None, help="来源标识（文件名/URL/备注）")
    p.add_argument(
        "--model",
        default=os.getenv("AGENT_OS_MODEL", "gpt-4o-mini"),
        help="用于 gatekeeper/extract 的模型",
    )
    p.add_argument(
        "--no-llm", action="store_true", help="不调用 LLM（仅做规则校验 + 最小字段入库）"
    )
    args = p.parse_args(argv)

    settings = Settings.from_env()
    store = asset_store_from_settings(enable=True, path=settings.asset_store_path)
    opt = IngestOptions(
        client_id=args.client_id,
        user_id=args.user_id,
        skill_id=args.skill,
        source=args.source,
        model=args.model,
        allow_llm=not args.no_llm,
        compliance_dir=settings.skill_compliance_dir,
    )
    if args.input.suffix.lower() == ".jsonl":
        r = ingest_jsonl(args.input, store=store, opt=opt)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    raw = args.input.read_text(encoding="utf-8")
    r = ingest_text(raw, store=store, opt=opt)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def _asset_rm_main(argv: list[str]) -> int:
    """按 case_id 删除单条，或按 client_id + skill 清空该 skill 下全部案例（回退/清库）。"""
    from agent_os.knowledge.asset_store import LanceDbAssetStore

    p = argparse.ArgumentParser(
        prog="agent-os-runtime asset-rm",
        description="删除 Asset Store（LanceDB）中的案例行。用于垃圾数据回退或清库。",
    )
    p.add_argument("--case-id", default=None, help="删除指定 case_id")
    p.add_argument("--client-id", default=None, help="与 --skill --all-skill 联用")
    p.add_argument("--skill", default=None, help="与 --client-id --all-skill 联用")
    p.add_argument(
        "--all-skill",
        action="store_true",
        help="删除该 tenant 下某 skill 的全部案例（危险操作）",
    )
    args = p.parse_args(argv)

    settings = Settings.from_env()
    store = LanceDbAssetStore(path=settings.asset_store_path)

    if args.case_id:
        r = store.delete_by_case_id(args.case_id.strip())
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("status") == "ok" else 1

    if args.all_skill and args.client_id and args.skill:
        r = store.delete_by_client_skill(args.client_id.strip(), args.skill.strip())
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("status") == "ok" else 1

    print("必须指定 --case-id，或同时指定 --client-id --skill --all-skill", file=sys.stderr)
    return 1


def _mcp_probe_server_main(argv: list[str]) -> int:
    from agent_os.mcp.probe_server import main as mcp_main

    return mcp_main(argv)


def _chat_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime",
        description="Agent OS Runtime（Agno + Mem0 + Hindsight + Graphiti 只读 + AsyncReview）",
    )
    p.add_argument("--client-id", default="demo_client", help="租户/客户 ID（必填隔离键）")
    p.add_argument("--user-id", default=None, help="终端用户 ID（可选）")
    p.add_argument("--task-id", default=None, help="任务 ID（写入 Hindsight / AsyncReview 关联）")
    p.add_argument("--slow", action="store_true", help="启用慢推理模式（Agno reasoning）")
    p.add_argument("--session-id", default=None, help="会话 ID（调试用，默认随机）")
    p.add_argument(
        "--no-knowledge", action="store_true", help="不挂载 search_domain_knowledge（仅 Mem0）"
    )
    p.add_argument(
        "--skill",
        default=None,
        help="Agent skill_id（默认见 AGENT_OS_DEFAULT_SKILL_ID，通常为 default_agent）。",
    )
    p.add_argument(
        "--no-async-review",
        action="store_true",
        help="退出时不运行 AsyncReview 复盘",
    )
    args = p.parse_args(argv)

    settings = Settings.from_env()
    ctrl = MemoryController.create_default(
        mem0_api_key=settings.mem0_api_key,
        mem0_host=settings.mem0_host,
        local_memory_path=settings.local_memory_path,
        hindsight_path=settings.hindsight_path,
        enable_hindsight=settings.enable_hindsight,
        snapshot_every_n_turns=settings.snapshot_every_n_turns,
        enable_memory_policy=settings.enable_memory_policy,
        memory_policy_mode=settings.memory_policy_mode,
    )

    knowledge = (
        None
        if args.no_knowledge
        else GraphitiReadService.from_env(settings.knowledge_fallback_path)
    )
    asset_store = asset_store_from_settings(
        enable=settings.enable_asset_store, path=settings.asset_store_path
    )

    skill_id = args.skill if args.skill is not None else None

    agent = get_agent(
        ctrl,
        client_id=args.client_id,
        user_id=args.user_id,
        thought_mode="slow" if args.slow else "fast",
        knowledge=knowledge,
        asset_store=asset_store,
        settings=settings,
        skill_id=skill_id,
        entrypoint="cli",
    )

    session_id = args.session_id or new_session_id()
    transcript: list[tuple[str, str]] = []
    print(f"会话 session_id={session_id} | client_id={args.client_id} | exit: quit / exit")
    while True:
        try:
            line = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break
        ctrl.bump_turn_and_maybe_snapshot(args.client_id, args.user_id)
        out = agent.run(
            line,
            session_id=session_id,
            user_id=args.user_id or args.client_id,
            stream=False,
        )
        content = out.content
        text = content if isinstance(content, str) else str(content)
        print(text)
        transcript.append(("user", line))
        transcript.append(("assistant", text))

    if not args.no_async_review and ctrl.hindsight_store is not None and transcript:
        review = AsyncReviewService.from_env(ctrl.hindsight_store)
        review.submit_and_wait(
            client_id=args.client_id,
            user_id=args.user_id,
            task_id=args.task_id,
            transcript=transcript,
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] == "doctor":
        return _doctor_main(argv[1:])
    if argv and argv[0] == "eval":
        return _eval_main(argv[1:])
    if argv and argv[0] == "knowledge-append-jsonl":
        return _knowledge_append_main(argv[1:])
    if argv and argv[0] == "graphiti-ingest":
        return _graphiti_ingest_main(argv[1:])
    if argv and argv[0] == "asset-ingest":
        return _asset_ingest_main(argv[1:])
    if argv and argv[0] == "asset-rm":
        return _asset_rm_main(argv[1:])
    if argv and argv[0] == "mcp-probe-server":
        return _mcp_probe_server_main(argv[1:])
    return _chat_main(argv)


if __name__ == "__main__":
    sys.exit(main())
