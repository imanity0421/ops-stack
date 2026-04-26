from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from agent_os.agent.factory import get_agent, new_session_id
from agent_os.agent.task_memory import TaskMemoryStore, TaskSummaryService
from agent_os.config import Settings
from agent_os.knowledge.graphiti_entitlements import (
    EntitlementsRevisionConflictError,
    append_entitlements_audit,
    load_entitlements_file,
    update_entitlements_file,
)
from agent_os.knowledge.asset_store import asset_store_from_settings
from agent_os.knowledge.graphiti_reader import GraphitiReadService
from agent_os.manifest_loader import load_skill_manifest_registry, resolve_effective_skill_id
from agent_os.memory.controller import MemoryController
from agent_os.memory.hindsight_store import HindsightStore
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
        help="skill_id，与运行时 AGENT_OS_DEFAULT_SKILL_ID 一致；写入系统级 Graphiti group_id(skill)",
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
    if not args.episodes_json.is_file():
        print(f"输入文件不存在: {args.episodes_json}", file=sys.stderr)
        return 1
    if args.dry_run:
        import json as _json

        try:
            raw = _json.loads(args.episodes_json.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, _json.JSONDecodeError) as e:
            print(f"无法读取或解析 JSON: {e}", file=sys.stderr)
            return 1
        if isinstance(raw, list):
            _ = raw
        elif isinstance(raw, dict) and isinstance(raw.get("episodes"), list):
            _ = raw["episodes"]
        else:
            print("JSON 须为数组或含 episodes 数组的对象", file=sys.stderr)
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
    p.add_argument(
        "--scope",
        choices=["system", "client_shared", "user_private"],
        default=None,
        help="资产作用域；默认按 client/user 推导，system 可配合 client-id=system_global 导入金牌案例",
    )
    p.add_argument(
        "--asset-type",
        choices=["style_reference", "source_material"],
        default=None,
        help="资产类型；未指定时由 LLM 分类，--no-llm 时默认 style_reference",
    )
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
    if not args.input.is_file():
        print(f"输入文件不存在: {args.input}", file=sys.stderr)
        return 1

    settings = Settings.from_env()
    store = asset_store_from_settings(enable=True, path=settings.asset_store_path)
    opt = IngestOptions(
        client_id=args.client_id,
        user_id=args.user_id,
        skill_id=args.skill,
        scope=args.scope,
        asset_type=args.asset_type,
        source=args.source,
        model=args.model,
        allow_llm=not args.no_llm,
        compliance_dir=settings.skill_compliance_dir,
    )
    if args.input.suffix.lower() == ".jsonl":
        r = ingest_jsonl(args.input, store=store, opt=opt)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        successful_rows = (
            int(r.get("accepted", 0) or 0)
            + int(r.get("quarantined", 0) or 0)
            + int(r.get("duplicate_skipped", 0) or 0)
        )
        if r.get("status") == "error" or successful_rows == 0:
            return 1
        return 0
    try:
        raw = args.input.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        print(f"无法读取输入文件: {e}", file=sys.stderr)
        return 1
    r = ingest_text(raw, store=store, opt=opt)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 1 if r.get("status") in ("error", "rejected") else 0


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


def _hindsight_index_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime hindsight-index",
        description="手动查看、重建或删除 Hindsight JSON sidecar / LanceDB vector sidecar。",
    )
    p.add_argument(
        "action",
        choices=[
            "status",
            "rebuild",
            "invalidate",
            "vector-status",
            "vector-rebuild",
            "vector-invalidate",
        ],
    )
    p.add_argument("--path", type=Path, default=None, help="Hindsight JSONL 路径；默认读取 Settings")
    p.add_argument("--vector-path", type=Path, default=None, help="Hindsight LanceDB 向量索引路径")
    args = p.parse_args(argv)

    settings = Settings.from_env()
    store = HindsightStore(
        args.path or settings.hindsight_path,
        enable_vector_recall=True,
        vector_index_path=args.vector_path or settings.hindsight_vector_index_path,
    )
    if args.action == "status":
        result = store.index_status()
    elif args.action == "rebuild":
        result = store.rebuild_index()
    elif args.action == "invalidate":
        result = {"status": "ok", "removed": store.invalidate_index()}
    elif args.action == "vector-status":
        result = store.vector_index_status()
    elif args.action == "vector-rebuild":
        result = store.rebuild_vector_index()
    else:
        result = {"status": "ok", "removed": store.invalidate_vector_index()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") == "error" else 0


def _mcp_probe_server_main(argv: list[str]) -> int:
    from agent_os.mcp.probe_server import main as mcp_main

    return mcp_main(argv)


def _graphiti_entitlements_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime graphiti-entitlements",
        description="管理 Graphiti 权限持久化文件（默认 data/graphiti_entitlements.json）",
    )
    p.add_argument(
        "--path",
        type=Path,
        default=Path(
            os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", "data/graphiti_entitlements.json")
        ),
        help="权限文件路径；未指定时优先取 AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH",
    )
    p.add_argument("--show", action="store_true", help="打印当前权限文件（默认动作）")
    p.add_argument(
        "--set-global", default=None, help="设置全局允许 skill 列表（逗号分隔，* 代表全开）"
    )
    p.add_argument("--client-id", default=None, help="设置/删除某 client 权限时需要")
    p.add_argument("--set-client", default=None, help="设置 client 允许 skill 列表（逗号分隔）")
    p.add_argument(
        "--remove-client", action="store_true", help="删除某 client 权限项（需 --client-id）"
    )
    p.add_argument(
        "--expected-revision",
        type=int,
        default=None,
        help="乐观并发控制：仅当当前 revision 匹配时才写入；不传则按最新 revision 写入",
    )
    args = p.parse_args(argv)

    doc = load_entitlements_file(args.path)
    actions: list[str] = []
    set_global_vals: list[str] | None = None
    set_client_vals: list[str] | None = None

    if args.set_global is not None:
        vals = sorted({x.strip() for x in args.set_global.split(",") if x.strip()})
        set_global_vals = vals
        doc["global_allowed_skill_ids"] = vals
        actions.append("set_global")

    if args.set_client is not None:
        if not args.client_id:
            print("--set-client 需要同时提供 --client-id", file=sys.stderr)
            return 1
        vals = sorted({x.strip() for x in args.set_client.split(",") if x.strip()})
        set_client_vals = vals
        doc["client_entitlements"][args.client_id] = vals
        actions.append("set_client")

    if args.remove_client:
        if not args.client_id:
            print("--remove-client 需要同时提供 --client-id", file=sys.stderr)
            return 1
        doc["client_entitlements"].pop(args.client_id, None)
        actions.append("remove_client")

    changed = args.set_global is not None or args.set_client is not None or args.remove_client
    if changed:

        def _mutate(cur: dict[str, object]) -> None:
            if set_global_vals is not None:
                cur["global_allowed_skill_ids"] = list(set_global_vals)
            raw_map = cur.get("client_entitlements")
            client_map = raw_map if isinstance(raw_map, dict) else {}
            if set_client_vals is not None and args.client_id:
                client_map[args.client_id] = list(set_client_vals)
            if args.remove_client and args.client_id:
                client_map.pop(args.client_id, None)
            cur["client_entitlements"] = client_map

        try:
            before, doc = update_entitlements_file(
                args.path,
                expected_revision=args.expected_revision,
                mutator=_mutate,
            )
        except EntitlementsRevisionConflictError as e:
            print(
                f"{e}；请先执行 `graphiti-entitlements --show` 获取最新 revision，再重试 --expected-revision {e.actual}",
                file=sys.stderr,
            )
            return 2
        actor = (
            os.getenv("AGENT_OS_ACTOR")
            or os.getenv("USERNAME")
            or os.getenv("USER")
            or "cli_unknown"
        )
        append_entitlements_audit(
            action="+".join(actions) if actions else "update",
            actor=actor,
            source="cli.graphiti-entitlements",
            entitlements_path=args.path,
            before=before,
            after=doc,
            metadata={
                "client_id": args.client_id,
                "show": bool(args.show),
                "expected_revision": args.expected_revision,
            },
        )

    if args.show or not changed:
        print(json.dumps({"path": str(args.path), "data": doc}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"status": "ok", "path": str(args.path)}, ensure_ascii=False, indent=2))
    return 0


def _chat_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime",
        description="Agent OS Runtime（Agno + Mem0 + Hindsight + Graphiti 只读 + AsyncReview）",
    )
    p.add_argument("--client-id", default="demo_client", help="租户或工作区隔离键（client_id）")
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
        memory_ledger_path=settings.memory_ledger_path,
        enable_hindsight=settings.enable_hindsight,
        enable_hindsight_vector_recall=settings.enable_hindsight_vector_recall,
        hindsight_vector_index_path=settings.hindsight_vector_index_path,
        hindsight_vector_score_weight=settings.hindsight_vector_score_weight,
        hindsight_vector_candidate_limit=settings.hindsight_vector_candidate_limit,
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

    session_id = args.session_id or new_session_id()
    skill_id = args.skill if args.skill is not None else None
    effective_skill_id = resolve_effective_skill_id(
        skill_id,
        settings.default_skill_id,
        load_skill_manifest_registry(settings.agent_manifest_dir),
    )
    task_store = (
        TaskMemoryStore(settings.task_memory_sqlite_path) if settings.enable_task_memory else None
    )
    task_summary_service = (
        TaskSummaryService(
            task_store,
            model=settings.task_summary_model,
            max_chars=settings.task_summary_max_chars,
            min_messages=settings.task_summary_min_messages,
            every_n_messages=settings.task_summary_every_n_messages,
        )
        if task_store is not None
        else None
    )
    active_task_id: str | None = None

    def _build_agent_for_turn():
        current_summary = None
        task_index = None
        if task_store is not None and active_task_id is not None:
            current_summary = task_store.get_summary(session_id=session_id, task_id=active_task_id)
            task_index = task_store.task_index(session_id=session_id)
        return get_agent(
            ctrl,
            client_id=args.client_id,
            user_id=args.user_id,
            thought_mode="slow" if args.slow else "fast",
            knowledge=knowledge,
            asset_store=asset_store,
            settings=settings,
            skill_id=skill_id,
            entrypoint="cli",
            current_task_summary=current_summary,
            session_task_index=task_index,
        )

    agent = None if task_store is not None else _build_agent_for_turn()
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
        if task_store is not None:
            task = task_store.get_or_create_active_task(
                session_id=session_id,
                client_id=args.client_id,
                user_id=args.user_id,
                skill_id=effective_skill_id,
                seed_message=line,
            )
            active_task_id = task.task_id
            task_store.append_message(
                session_id=session_id,
                task_id=active_task_id,
                role="user",
                content=line,
            )
            agent = _build_agent_for_turn()
        ctrl.bump_turn_and_maybe_snapshot(args.client_id, args.user_id)
        if agent is None:
            agent = _build_agent_for_turn()
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
        if task_store is not None and active_task_id is not None:
            task_store.append_message(
                session_id=session_id,
                task_id=active_task_id,
                role="assistant",
                content=text,
            )
            if task_summary_service is not None:
                task_summary_service.maybe_update(session_id=session_id, task_id=active_task_id)

    if not args.no_async_review and ctrl.hindsight_store is not None and transcript:
        review = AsyncReviewService.from_env(ctrl)
        review.submit_and_wait(
            client_id=args.client_id,
            user_id=args.user_id,
            task_id=args.task_id or active_task_id,
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
    if argv and argv[0] == "hindsight-index":
        return _hindsight_index_main(argv[1:])
    if argv and argv[0] == "mcp-probe-server":
        return _mcp_probe_server_main(argv[1:])
    if argv and argv[0] == "graphiti-entitlements":
        return _graphiti_entitlements_main(argv[1:])
    return _chat_main(argv)


if __name__ == "__main__":
    sys.exit(main())
