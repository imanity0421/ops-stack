#!/usr/bin/env python3
"""
跨机器开发记录：追加 SESSION_LOG.md 或写入 runs.jsonl。

用法:
  python log_session.py session --title "标题" --body "多行\n正文"
  python log_session.py run --cmd "pytest -q" --exit-code 0 [--cwd PATH] [--note TEXT]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parent


def _machine() -> str:
    return (os.environ.get("CODING_SYNC_MACHINE") or os.environ.get("COMPUTERNAME") or "unknown").strip()


def _now_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def cmd_session(args: argparse.Namespace) -> int:
    log_md = _root() / "SESSION_LOG.md"
    title = (args.title or "").strip()
    body = (args.body or "").strip()
    if not title:
        print("ERROR: --title 必填", file=sys.stderr)
        return 1
    block = f"""
---

## {_now_local_iso()} | {_machine()}

**标题**：{title}

{body if body else "（无正文）"}

"""
    with log_md.open("a", encoding="utf-8") as f:
        f.write(block)
    print(f"OK -> {log_md}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    runs = _root() / "runs.jsonl"
    cmd = (args.shell_command or "").strip()
    if not cmd:
        print("ERROR: --cmd 必填", file=sys.stderr)
        return 1
    cwd = args.cwd or os.getcwd()
    note = (args.note or "").strip()
    rec = {
        "ts": _now_local_iso(),
        "machine": _machine(),
        "cwd": cwd,
        "command": cmd,
        "exit_code": int(args.exit_code),
    }
    if note:
        rec["note"] = note
    line = json.dumps(rec, ensure_ascii=False)
    with runs.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"OK -> {runs}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="coding-sync 日志工具")
    sub = p.add_subparsers(dest="action", required=True)

    ps = sub.add_parser("session", help="追加 SESSION_LOG.md")
    ps.add_argument("--title", "-t", required=True, help="会话标题")
    ps.add_argument("--body", "-b", default="", help="正文（可多行）")

    pr = sub.add_parser("run", help="追加 runs.jsonl 一条")
    pr.add_argument(
        "--cmd",
        "-c",
        dest="shell_command",
        required=True,
        help="执行的命令行字符串",
    )
    pr.add_argument("--exit-code", type=int, default=0, help="退出码")
    pr.add_argument("--cwd", type=str, default=None, help="工作目录（默认当前目录）")
    pr.add_argument("--note", "-n", default="", help="备注")

    args = p.parse_args()
    if args.action == "session":
        return cmd_session(args)
    if args.action == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
