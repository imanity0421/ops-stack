from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from agent_os.agent.compact import (
    CompactSummaryRecord,
    CompactSummaryService,
    compact_summary_from_json,
)
from agent_os.agent.factory import get_agent, new_session_id
from agent_os.agent.task_memory import TaskMemoryStore, TaskSummaryService
from agent_os.config import Settings
from agent_os.context_builder import (
    ArtifactContextRef,
    ContextCharBudget,
    ContextBuilder,
    build_auto_retrieval_context,
    effective_session_history_max_messages,
    resolve_auto_retrieve_decision,
)
from agent_os.context_diagnostics import (
    build_context_diagnostics,
    format_context_diagnostics_markdown,
)
from agent_os.cte.branch_task import branch_task
from agent_os.cte.resume_task import resume_task
from agent_os.observability import log_context_management_trace
from agent_os.knowledge.graphiti_entitlements import (
    EntitlementsRevisionConflictError,
    append_entitlements_audit,
    load_entitlements_file,
    update_entitlements_file,
)
from agent_os.knowledge.asset_store import asset_store_from_settings
from agent_os.knowledge.artifact_store import ArtifactRecord, ArtifactStore
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


def _task_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime task",
        description="Stage 2 Task Entity v0：创建、列出与软归档任务。",
    )
    sub = p.add_subparsers(dest="action", required=True)

    p_new = sub.add_parser("new", help="创建 task，并绑定一个主线 session")
    p_new.add_argument("name", help="用户可见任务名")
    p_new.add_argument("--session-id", default=None, help="主线 session id；默认新建")

    p_list = sub.add_parser("list", help="列出 task")
    p_list.add_argument("--include-archived", action="store_true", help="包含 archived task")
    p_list.add_argument("--limit", type=int, default=50, help="最大返回条数")

    p_archive = sub.add_parser("archive", help="软归档 task")
    p_archive.add_argument("task_id")

    p_unarchive = sub.add_parser("unarchive", help="恢复 archived task")
    p_unarchive.add_argument("task_id")

    p_resume = sub.add_parser("resume", help="Stage 4：恢复 task 工作面")
    p_resume.add_argument("task_id")
    p_resume.add_argument("--from-session-id", default=None)
    p_resume.add_argument("--force-fork", action="store_true")
    p_resume.add_argument("--force-connect", action="store_true")
    p_resume.add_argument("--json", action="store_true")

    p_branch = sub.add_parser("branch", help="Stage 4：从 task final_state 开分支 session")
    p_branch.add_argument("task_id")
    p_branch.add_argument("--from-session-id", default=None)
    p_branch.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    settings = Settings.from_env()
    store = TaskMemoryStore(settings.task_memory_sqlite_path)

    if args.action == "resume":
        if args.force_fork and args.force_connect:
            print(json.dumps({"status": "error", "reason": "conflicting_force_flags"}, ensure_ascii=False))
            return 1
        force_mode = "fork" if args.force_fork else "connect" if args.force_connect else None
        result = resume_task(
            store=store,
            task_id=args.task_id,
            from_session_id=args.from_session_id,
            force_mode=force_mode,
            session_id_factory=new_session_id,
            artifact_store=ArtifactStore(settings.artifact_store_path),
            context_char_budget=settings.context_max_chars,
        )
        payload = result.to_dict()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif result.status == "ok" and result.decision is not None and result.final_state is not None:
            print(
                f"resume {result.decision.connect_or_fork} "
                f"{result.decision.source_session_id} -> {result.decision.target_session_id}"
            )
            print(result.final_state.prompt)
        else:
            print(json.dumps(payload, ensure_ascii=False))
        return 0 if result.status == "ok" else 1

    if args.action == "branch":
        result = branch_task(
            store=store,
            task_id=args.task_id,
            from_session_id=args.from_session_id,
            session_id_factory=new_session_id,
            artifact_store=ArtifactStore(settings.artifact_store_path),
            context_char_budget=settings.context_max_chars,
        )
        payload = result.to_dict()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif result.status == "ok" and result.branch_session is not None:
            print(
                f"branch {result.source_session.session_id if result.source_session else ''} "
                f"-> {result.branch_session.session_id}"
            )
            if result.final_state is not None:
                print(result.final_state.prompt)
        else:
            print(json.dumps(payload, ensure_ascii=False))
        return 0 if result.status == "ok" else 1

    if args.action == "new":
        session_id = args.session_id or new_session_id()
        task = store.create_task(name=args.name, current_main_session_id=session_id)
        store.upsert_session(
            session_id=session_id,
            client_id="task_cli",
            active_task_id=task.task_id,
        )
        print(json.dumps({"status": "ok", "task": task.__dict__}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "list":
        tasks = store.list_task_entities(
            include_archived=args.include_archived,
            limit=args.limit,
        )
        print(
            json.dumps(
                {"status": "ok", "tasks": [task.__dict__ for task in tasks]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.action == "archive":
        task = store.archive_task_entity(args.task_id)
    else:
        task = store.unarchive_task_entity(args.task_id)
    if task is None:
        print(json.dumps({"status": "error", "reason": "task_not_found"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "task": task.__dict__}, ensure_ascii=False, indent=2))
    return 0


def _artifact_record_dict(record: ArtifactRecord, *, include_raw: bool = False) -> dict[str, object]:
    data: dict[str, object] = {
        "artifact_id": record.artifact_id,
        "task_id": record.task_id,
        "session_id": record.session_id,
        "status": record.status,
        "digest": record.ref_digest,
        "digest_status": record.digest_status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "stable_key": record.stable_key,
        "originating_session_id": record.originating_session_id,
    }
    if include_raw:
        data["raw_content"] = record.raw_content
    return data


def _artifact_store_from_env() -> ArtifactStore:
    return ArtifactStore(Settings.from_env().artifact_store_path)


def _artifact_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime artifact",
        description="Stage 2 Artifact Lifecycle：列出、查看与软归档 artifact。",
    )
    sub = p.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="按 task_id 列出 artifact")
    p_list.add_argument("--task-id", required=True)
    p_list.add_argument("--include-archived", action="store_true")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="按 artifact_id 查看 artifact")
    p_show.add_argument("artifact_id")
    p_show.add_argument("--raw", action="store_true", help="只输出原文")
    p_show.add_argument("--json", action="store_true")

    p_archive = sub.add_parser("archive", help="软归档 artifact")
    p_archive.add_argument("artifact_id")
    p_archive.add_argument("--json", action="store_true")

    p_update = sub.add_parser("update", help="更新 artifact；跨 session 自动 CoW")
    p_update.add_argument("artifact_id")
    p_update.add_argument("--session-id", required=True)
    p_update.add_argument("--raw-content", required=True)
    p_update.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    settings = Settings.from_env()
    store = ArtifactStore(settings.artifact_store_path)

    if args.action == "list":
        records = store.list_artifacts(
            task_id=args.task_id,
            include_archived=args.include_archived,
            limit=args.limit,
        )
        payload = {
            "status": "ok",
            "artifacts": [_artifact_record_dict(record) for record in records],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if not records:
                print("No artifacts found.")
            for record in records:
                print(
                    f"{record.artifact_id}\t{record.status}\t{record.task_id}\t"
                    f"{record.digest_status}\t{record.ref_digest}"
                )
        return 0

    if args.action == "show":
        record = store.get_artifact(args.artifact_id)
        if record is None:
            print(json.dumps({"status": "error", "reason": "artifact_not_found"}, ensure_ascii=False))
            return 1
        if args.raw:
            print(record.raw_content)
            return 0
        payload = {"status": "ok", "artifact": _artifact_record_dict(record, include_raw=True)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"artifact_id: {record.artifact_id}")
            print(f"task_id: {record.task_id}")
            print(f"session_id: {record.session_id}")
            print(f"status: {record.status}")
            print(f"digest_status: {record.digest_status}")
            print(f"digest: {record.ref_digest}")
            print("")
            print(record.raw_content)
        return 0

    if args.action == "update":
        result = store.update_artifact_content(
            artifact_id=args.artifact_id,
            current_session_id=args.session_id,
            raw_content=args.raw_content,
            task_memory_db_path=settings.task_memory_sqlite_path,
        )
        if result is None:
            print(json.dumps({"status": "error", "reason": "artifact_not_found"}, ensure_ascii=False))
            return 1
        payload = {
            "status": "ok",
            "mode": result.mode,
            "cow_from": result.cow_from,
            "compact_refs_updated": result.compact_refs_updated,
            "artifact": _artifact_record_dict(result.artifact, include_raw=True),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if result.cow_from:
                print(f"copied {result.cow_from} -> {result.artifact.artifact_id}")
            else:
                print(f"updated {result.artifact.artifact_id}")
        return 0

    record = store.archive_artifact(args.artifact_id)
    if record is None:
        print(json.dumps({"status": "error", "reason": "artifact_not_found"}, ensure_ascii=False))
        return 1
    payload = {"status": "ok", "artifact": _artifact_record_dict(record)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"archived {record.artifact_id}")
    return 0


def _blob_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime blob",
        description="Blob maintenance helpers. Destructive GC is intentionally unavailable.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    p_gc = sub.add_parser("gc", help="只列出 GC 候选，不删除")
    p_gc.add_argument("--orphan", action="store_true", required=True, help="列出无有效 task 的 artifact")
    p_gc.add_argument("--include-archived", action="store_true")
    p_gc.add_argument("--limit", type=int, default=200)
    p_gc.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    settings = Settings.from_env()
    task_store = TaskMemoryStore(settings.task_memory_sqlite_path)
    existing_task_ids = {
        task.task_id
        for task in task_store.list_task_entities(include_archived=True, limit=max(1, args.limit * 2))
    }
    records = ArtifactStore(settings.artifact_store_path).list_orphan_artifacts(
        existing_task_ids=existing_task_ids,
        include_archived=args.include_archived,
        limit=args.limit,
    )
    payload = {
        "status": "ok",
        "dry_run": True,
        "orphan_artifacts": [_artifact_record_dict(record) for record in records],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not records:
            print("No orphan artifacts found.")
        for record in records:
            print(
                f"{record.artifact_id}\t{record.status}\t{record.task_id}\t"
                f"{record.updated_at}"
            )
    return 0


def _compact_record_dict(record: object) -> dict[str, object]:
    return record.to_dict()  # type: ignore[no-any-return,attr-defined]


def _compact_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime compact",
        description="Stage 3 CompactSummary：手动生成与查看结构化 compact 摘要。",
    )
    sub = p.add_subparsers(dest="action", required=True)

    p_run = sub.add_parser("run", help="对指定 session/task 执行手动 compact")
    p_run.add_argument("--session-id", required=True)
    p_run.add_argument("--task-id", required=True)
    p_run.add_argument("--artifact-ref", action="append", default=[])
    p_run.add_argument("--pinned-ref", action="append", default=[])
    p_run.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="查看最近 CompactSummary")
    p_show.add_argument("--session-id", required=True)
    p_show.add_argument("--task-id", required=True)
    p_show.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    settings = Settings.from_env()
    if not settings.compact_enabled:
        print(json.dumps({"status": "error", "reason": "compact_disabled"}, ensure_ascii=False))
        return 1
    store = TaskMemoryStore(settings.task_memory_sqlite_path)

    if args.action == "run":
        record = CompactSummaryService(store, model=settings.compact_model).compact(
            session_id=args.session_id,
            task_id=args.task_id,
            current_artifact_refs=list(args.artifact_ref or []),
            pinned_refs=list(args.pinned_ref or []),
        )
        if record is None:
            print(json.dumps({"status": "error", "reason": "no_messages"}, ensure_ascii=False))
            return 1
        payload = {"status": "ok", "compact_summary": _compact_record_dict(record)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"compact summary v{record.summary_version} for task {record.task_id}")
            print(record.summary.model_dump_json(indent=2))
        return 0

    record = store.get_compact_summary(session_id=args.session_id, task_id=args.task_id)
    if record is None:
        print(json.dumps({"status": "error", "reason": "compact_summary_not_found"}, ensure_ascii=False))
        return 1
    payload = {"status": "ok", "compact_summary": _compact_record_dict(record)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"compact summary v{record.summary_version} for task {record.task_id}")
        print(record.summary.model_dump_json(indent=2))
    return 0


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
    p.add_argument(
        "--path", type=Path, default=None, help="Hindsight JSONL 路径；默认读取 Settings"
    )
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


def _load_diagnostic_history(path: Path | None) -> list[object]:
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 history JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("history JSON 须为数组")
    messages: list[object] = []
    for item in raw:
        if isinstance(item, dict):
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            messages.append((role, content))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            messages.append((str(item[0]), str(item[1])))
    return messages


def _load_diagnostic_artifact_refs(path: Path | None) -> list[ArtifactContextRef]:
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 artifact refs JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("artifact refs JSON 须为数组")
    refs: list[ArtifactContextRef] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or item.get("ref") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        digest = str(item.get("digest") or "").strip()
        if not artifact_id or not digest:
            continue
        refs.append(
            ArtifactContextRef(
                artifact_id=artifact_id,
                task_id=task_id,
                digest=digest,
                digest_status=str(item.get("digest_status") or "built"),
                purpose=str(item.get("purpose") or ""),
            )
        )
    return refs


def _load_diagnostic_compact_summary(path: Path | None) -> CompactSummaryRecord | None:
    if path is None:
        return None
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
        raw = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 compact summary JSON: {exc}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("summary"), dict):
        summary = compact_summary_from_json(json.dumps(raw["summary"], ensure_ascii=False))
        return CompactSummaryRecord(
            session_id=str(raw.get("session_id") or "diagnostic_session"),
            task_id=str(raw.get("task_id") or "diagnostic_task"),
            summary_version=int(raw.get("summary_version") or 1),
            summary=summary,
            covered_message_start_id=raw.get("covered_message_start_id"),
            covered_message_end_id=raw.get("covered_message_end_id"),
            covered_message_count=int(raw.get("covered_message_count") or 0),
            updated_at=str(raw.get("updated_at") or ""),
            compact_model=str(raw.get("compact_model") or "diagnostic"),
            compact_policy_version=str(raw.get("compact_policy_version") or "compact_summary_v1"),
            status=str(raw.get("status") or "active"),
        )
    return CompactSummaryRecord(
        session_id="diagnostic_session",
        task_id="diagnostic_task",
        summary_version=1,
        summary=compact_summary_from_json(raw_text),
        covered_message_count=0,
        updated_at="",
        compact_model="diagnostic",
    )


def _context_diagnose_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="agent-os-runtime context-diagnose",
        description="构造一轮 ContextBuilder 消息并输出 /context 诊断；不调用模型。",
    )
    p.add_argument("--message", "-m", required=True, help="要诊断的当前用户消息")
    p.add_argument("--client-id", default="demo_client", help="租户或工作区隔离键")
    p.add_argument("--user-id", default=None, help="终端用户 ID（可选）")
    p.add_argument("--skill", default=None, help="skill_id；默认按 Settings / manifest 解析")
    p.add_argument(
        "--entrypoint",
        choices=["cli", "web", "api"],
        default="cli",
        help="用于 runtime_context 的入口标识",
    )
    p.add_argument("--history-json", type=Path, default=None, help="可选历史消息 JSON 数组")
    p.add_argument("--artifact-refs-json", type=Path, default=None, help="可选 artifact refs JSON 数组")
    p.add_argument("--compact-summary-json", type=Path, default=None, help="可选 CompactSummary JSON")
    p.add_argument("--retrieved-context-file", type=Path, default=None, help="可选外部召回文本")
    p.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    p.add_argument(
        "--fail-on-budget",
        choices=["warning", "danger", "over_budget"],
        default=None,
        help="当预算状态达到指定级别时以退出码 2 结束，便于把 /context 用作预检门禁",
    )
    args = p.parse_args(argv)

    settings = Settings.from_env()
    builder = ContextBuilder(
        timezone_name=settings.runtime_timezone,
        history_max_messages=settings.session_history_max_messages,
        include_runtime_context=settings.enable_ephemeral_metadata,
        max_tool_output_chars=settings.context_tool_output_max_chars,
        max_tool_outputs_total_chars=settings.context_tool_outputs_total_max_chars,
        context_char_budget=ContextCharBudget.from_total(settings.context_max_chars),
        enable_token_estimate=settings.context_estimate_tokens,
        hard_total_budget=settings.context_hard_budget,
        self_heal_over_budget=settings.context_self_heal_over_budget,
    )
    registry = load_skill_manifest_registry(settings.agent_manifest_dir)
    effective_skill_id = resolve_effective_skill_id(args.skill, settings.default_skill_id, registry)
    try:
        session_messages = _load_diagnostic_history(args.history_json)
        artifact_refs = _load_diagnostic_artifact_refs(args.artifact_refs_json)
        compact_summary = _load_diagnostic_compact_summary(args.compact_summary_json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    retrieved_context = None
    if args.retrieved_context_file is not None:
        try:
            retrieved_context = args.retrieved_context_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"无法读取 retrieved context: {exc}", file=sys.stderr)
            return 1
    bundle = builder.build_turn_message(
        args.message,
        entrypoint=args.entrypoint,
        client_id=args.client_id,
        user_id=args.user_id,
        skill_id=effective_skill_id,
        session_messages=session_messages,
        current_compact_summary=compact_summary,
        artifact_refs=artifact_refs,
        retrieved_context=retrieved_context,
    )
    diagnostics = build_context_diagnostics(bundle)
    if args.json:
        print(json.dumps(diagnostics.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_context_diagnostics_markdown(diagnostics))
    if args.fail_on_budget:
        severity = {"ok": 0, "unbounded": 0, "warning": 1, "danger": 2, "over_budget": 3}
        threshold = severity[args.fail_on_budget]
        if severity.get(diagnostics.budget_status, 0) >= threshold:
            return 2
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
    manifest_registry = load_skill_manifest_registry(settings.agent_manifest_dir)
    effective_skill_id = resolve_effective_skill_id(
        skill_id,
        settings.default_skill_id,
        manifest_registry,
    )
    effective_manifest = manifest_registry.get(effective_skill_id)
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
    context_builder = (
        ContextBuilder(
            timezone_name=settings.runtime_timezone,
            history_max_messages=settings.session_history_max_messages,
            include_runtime_context=settings.enable_ephemeral_metadata,
            max_tool_output_chars=settings.context_tool_output_max_chars,
            max_tool_outputs_total_chars=settings.context_tool_outputs_total_max_chars,
            context_char_budget=ContextCharBudget.from_total(settings.context_max_chars),
            enable_token_estimate=settings.context_estimate_tokens,
            hard_total_budget=settings.context_hard_budget,
            self_heal_over_budget=settings.context_self_heal_over_budget,
        )
        if settings.enable_context_builder
        else None
    )
    active_task_id: str | None = None

    def _task_context():
        current_summary = None
        compact_summary = None
        task_index = None
        if task_store is not None and active_task_id is not None:
            current_summary = task_store.get_summary(session_id=session_id, task_id=active_task_id)
            compact_summary = task_store.get_compact_summary(
                session_id=session_id, task_id=active_task_id
            )
            task_index = task_store.task_index(session_id=session_id)
        return current_summary, compact_summary, task_index

    def _build_agent_for_turn():
        current_summary, _compact_summary, task_index = _task_context()
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

    def _session_messages_for_context(limit: int) -> list[object]:
        if context_builder is None or agent is None or getattr(agent, "db", None) is None:
            return []
        getter = getattr(agent, "get_session_messages", None)
        if not callable(getter):
            return []
        effective_limit = max(0, int(limit))
        if effective_limit <= 0:
            return []
        try:
            return list(
                getter(
                    session_id=session_id,
                    limit=effective_limit,
                    skip_history_messages=False,
                )
            )
        except Exception:
            return []

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
        run_message = line
        if context_builder is not None:
            current_summary, compact_summary, task_index = _task_context()
            retrieved_context = None
            retrieve_mode = (
                effective_manifest.auto_retrieve_mode
                if effective_manifest and effective_manifest.auto_retrieve_mode
                else settings.context_auto_retrieve_mode
            )
            retrieve_keywords = (
                tuple(effective_manifest.auto_retrieve_keywords)
                if effective_manifest and effective_manifest.auto_retrieve_keywords
                else settings.context_auto_retrieve_keywords
            )
            retrieve_decision = resolve_auto_retrieve_decision(
                line, mode=retrieve_mode, keywords=retrieve_keywords
            )
            if settings.enable_context_auto_retrieve and retrieve_decision.enabled:
                retrieved_context = build_auto_retrieval_context(
                    ctrl,
                    line,
                    client_id=args.client_id,
                    user_id=args.user_id,
                    skill_id=effective_skill_id,
                    enable_hindsight=settings.enable_hindsight,
                    enable_temporal_grounding=settings.enable_temporal_grounding,
                    knowledge=knowledge,
                    enable_asset_store=settings.enable_asset_store,
                    asset_store=asset_store,
                    enable_hindsight_synthesis=settings.enable_hindsight_synthesis,
                    hindsight_synthesis_model=settings.hindsight_synthesis_model,
                    hindsight_synthesis_max_candidates=settings.hindsight_synthesis_max_candidates,
                    enable_asset_synthesis=settings.enable_asset_synthesis,
                    asset_synthesis_model=settings.asset_synthesis_model,
                    asset_synthesis_max_candidates=settings.asset_synthesis_max_candidates,
                )
            hist_cap = effective_session_history_max_messages(
                base_max_messages=settings.session_history_max_messages,
                task_summary=current_summary,
                cap_when_summary_present=settings.session_history_cap_when_task_summary,
            )
            bundle = context_builder.build_turn_message(
                line,
                entrypoint="cli",
                client_id=args.client_id,
                user_id=args.user_id,
                skill_id=effective_skill_id,
                session_messages=_session_messages_for_context(hist_cap),
                retrieved_context=retrieved_context,
                current_task_summary=current_summary,
                current_compact_summary=compact_summary,
                session_task_index=task_index,
                history_max_messages_override=hist_cap,
                auto_retrieve_reason=(
                    retrieve_decision.reason if settings.enable_context_auto_retrieve else None
                ),
            )
            run_message = bundle.message
            if settings.context_trace_log:
                log_context_management_trace(
                    request_id="-",
                    session_id=session_id,
                    trace=bundle.trace,
                    route="cli",
                )
        out = agent.run(
            run_message,
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
    if argv and argv[0] == "task":
        return _task_main(argv[1:])
    if argv and argv[0] == "artifact":
        return _artifact_main(argv[1:])
    if argv and argv[0] == "blob":
        return _blob_main(argv[1:])
    if argv and argv[0] == "compact":
        return _compact_main(argv[1:])
    if argv and argv[0] == "hindsight-index":
        return _hindsight_index_main(argv[1:])
    if argv and argv[0] == "mcp-probe-server":
        return _mcp_probe_server_main(argv[1:])
    if argv and argv[0] == "graphiti-entitlements":
        return _graphiti_entitlements_main(argv[1:])
    if argv and argv[0] in ("context-diagnose", "context"):
        return _context_diagnose_main(argv[1:])
    return _chat_main(argv)


if __name__ == "__main__":
    sys.exit(main())
