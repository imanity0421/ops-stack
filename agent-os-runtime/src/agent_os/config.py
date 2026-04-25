from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_SKILL_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
# 与 agent/skills/loader 一致：可加载子包名
_SKILL_PKG = re.compile(r"^[a-zA-Z0-9_]+$")


@dataclass(frozen=True)
class Settings:
    """从环境变量读取配置；未设置项使用安全默认值。"""

    openai_api_key: str | None = None
    openai_api_base: str | None = None
    mem0_api_key: str | None = None
    mem0_host: str | None = None
    snapshot_every_n_turns: int = 5
    local_memory_path: Path = Path("data/local_memory.json")
    hindsight_path: Path = Path("data/hindsight.jsonl")
    #: 是否启用 Hindsight 存储与相关工具（默认开启）
    enable_hindsight: bool = True
    #: 是否允许 Agent 写入 Mem0（record_client_*），默认开启；关闭后仍可读取画像（search_client_memory）
    enable_mem0_learning: bool = True
    #: 是否启用参考案例库（Asset Store / LanceDB），默认关闭（未配置时仍可裸跑）
    enable_asset_store: bool = False
    #: Asset Store 本地路径（LanceDB 目录）
    asset_store_path: Path = Path("data/asset_store.lancedb")
    knowledge_fallback_path: Path | None = None
    handoff_manifest_path: Path | None = None
    golden_rules_path: Path | None = None
    #: 每 skill 硬合规规则目录：``<dir>/<skill_id>.json``，格式同 Golden rules
    skill_compliance_dir: Path | None = None
    mcp_probe_fixture_path: Path | None = None
    #: 可选；若设置则扫描其中 ``*.json`` 覆盖/增补内置 skill 配方（见 ``manifest_loader``）。
    agent_manifest_dir: Path | None = None
    #: 未显式传 ``skill_id`` 时使用的默认 skill（须存在于注册表，通常为 ``default_agent``）。
    default_skill_id: str = "default_agent"
    #: 允许动态加载的 ``agent_os.agent.skills.<name>`` 子包名（逗号分隔）；空则**不**加载任何技能包
    skill_packages_allowlist: frozenset[str] = frozenset()
    #: 是否将 Agno 会话/运行元数据落库，并在下一轮注入**最近 N 条**到模型上文（N 见下项）
    enable_session_db: bool = True
    #: 单机默认 Sqlite 文件路径；若设置 ``AGENT_OS_SESSION_DB_URL`` 则本项仅在不使用 URL 时生效
    session_sqlite_path: Path = Path("data/agno_session.db")
    #: 非空时优先于 ``session_sqlite_path``：``sqlite:``/``postgres(ql)://``/``redis://``/``rediss://``，或无 ``://`` 的本地路径字符串
    session_db_url: str | None = None
    #: 将历史拼入模型上文时的最大消息条数；0 表示**仍落库**但不把历史拼进 context
    session_history_max_messages: int = 20
    #: P1-3 是否在 system 最前注入「系统宪法·冲突解决序」
    enable_constitutional_prompt: bool = True
    #: 是否注入当轮临时环境元数据（只进 prompt，不落长期记忆）
    enable_ephemeral_metadata: bool = True
    #: 临时环境元数据使用的本地时区
    runtime_timezone: str = "Asia/Shanghai"
    #: 是否启用 Memory Policy 服务端 gate，防止脏记忆写入
    enable_memory_policy: bool = True
    #: Memory Policy 模式：reject 拒写；warn 仅记录日志后放行
    memory_policy_mode: str = "reject"
    #: 是否在记忆检索渲染中带时间戳
    enable_temporal_grounding: bool = True
    #: 是否启用同一 session 内的 Task-aware Working Memory（首批为 store/prompt 注入）
    enable_task_memory: bool = False
    #: Task-aware Working Memory 本地 SQLite 路径
    task_memory_sqlite_path: Path = Path("data/task_memory.db")
    #: 当前 task summary 的最大建议长度（生成器使用；store 不强截断）
    task_summary_max_chars: int = 800

    @classmethod
    def from_env(cls) -> Settings:
        fb = os.getenv("AGENT_OS_KNOWLEDGE_FALLBACK_PATH")
        ho = os.getenv("AGENT_OS_HANDOFF_MANIFEST_PATH")
        gr = os.getenv("AGENT_OS_GOLDEN_RULES_PATH")
        sc = os.getenv("AGENT_OS_SKILL_COMPLIANCE_DIR")
        mp = os.getenv("AGENT_OS_MCP_PROBE_FIXTURE_PATH")
        am_dir = os.getenv("AGENT_OS_MANIFEST_DIR")
        raw_skill = (os.getenv("AGENT_OS_DEFAULT_SKILL_ID") or "default_agent").strip()
        default_skill_id = raw_skill if _SKILL_ID.match(raw_skill) else "default_agent"

        allow_raw = os.getenv("AGENT_OS_LOADABLE_SKILL_PACKAGES", "")
        allow_set: set[str] = set()
        for p in allow_raw.split(","):
            t = p.strip()
            if not t:
                continue
            if not _SKILL_PKG.match(t):
                raise ValueError(
                    f"AGENT_OS_LOADABLE_SKILL_PACKAGES 项非法: {t!r}，仅允许 [a-zA-Z0-9_]+"
                )
            allow_set.add(t)

        sdb = os.getenv("AGENT_OS_SESSION_DB_PATH", "data/agno_session.db")
        sdb_url = os.getenv("AGENT_OS_SESSION_DB_URL")
        hist_n = int(os.getenv("AGENT_OS_SESSION_HISTORY_MAX_MESSAGES", "20"))
        if hist_n < 0:
            raise ValueError("AGENT_OS_SESSION_HISTORY_MAX_MESSAGES 须 >= 0")

        enable_const = os.getenv("AGENT_OS_ENABLE_CONSTITUTIONAL", "1").lower() not in (
            "0",
            "false",
            "no",
        )
        policy_mode = os.getenv("AGENT_OS_MEMORY_POLICY_MODE", "reject").strip().lower()
        if policy_mode not in ("reject", "warn"):
            raise ValueError("AGENT_OS_MEMORY_POLICY_MODE 须为 reject 或 warn")

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_api_base=os.getenv("OPENAI_API_BASE"),
            mem0_api_key=os.getenv("MEM0_API_KEY"),
            mem0_host=os.getenv("MEM0_HOST"),
            snapshot_every_n_turns=int(os.getenv("AGENT_OS_SNAPSHOT_EVERY_N_TURNS", "5")),
            local_memory_path=Path(
                os.getenv("AGENT_OS_LOCAL_MEMORY_PATH", "data/local_memory.json")
            ),
            hindsight_path=Path(
                os.getenv(
                    "AGENT_OS_HISTORICAL_PATH",
                    os.getenv("AGENT_OS_HISTORICAL_STUB_PATH", "data/hindsight.jsonl"),
                )
            ),
            enable_hindsight=os.getenv("AGENT_OS_ENABLE_HINDSIGHT", "1").lower()
            not in ("0", "false", "no"),
            enable_mem0_learning=os.getenv("AGENT_OS_ENABLE_MEM0_LEARNING", "1").lower()
            not in ("0", "false", "no"),
            enable_asset_store=os.getenv("AGENT_OS_ENABLE_ASSET_STORE", "0").lower()
            in ("1", "true", "yes"),
            asset_store_path=Path(
                os.getenv("AGENT_OS_ASSET_STORE_PATH", "data/asset_store.lancedb")
            ),
            knowledge_fallback_path=Path(fb) if fb else None,
            handoff_manifest_path=Path(ho) if ho else None,
            golden_rules_path=Path(gr) if gr else None,
            skill_compliance_dir=Path(sc) if sc else None,
            mcp_probe_fixture_path=Path(mp) if mp else None,
            agent_manifest_dir=Path(am_dir) if am_dir else None,
            default_skill_id=default_skill_id,
            skill_packages_allowlist=frozenset(allow_set),
            enable_session_db=os.getenv("AGENT_OS_ENABLE_SESSION_DB", "1").lower()
            not in ("0", "false", "no"),
            session_sqlite_path=Path(sdb),
            session_db_url=(sdb_url.strip() or None) if sdb_url else None,
            session_history_max_messages=hist_n,
            enable_constitutional_prompt=enable_const,
            enable_ephemeral_metadata=os.getenv("AGENT_OS_ENABLE_EPHEMERAL_METADATA", "1").lower()
            not in ("0", "false", "no"),
            runtime_timezone=os.getenv("AGENT_OS_TIMEZONE", "Asia/Shanghai"),
            enable_memory_policy=os.getenv("AGENT_OS_ENABLE_MEMORY_POLICY", "1").lower()
            not in ("0", "false", "no"),
            memory_policy_mode=policy_mode,
            enable_temporal_grounding=os.getenv("AGENT_OS_ENABLE_TEMPORAL_GROUNDING", "1").lower()
            not in ("0", "false", "no"),
            enable_task_memory=os.getenv("AGENT_OS_ENABLE_TASK_MEMORY", "0").lower()
            in ("1", "true", "yes"),
            task_memory_sqlite_path=Path(
                os.getenv("AGENT_OS_TASK_MEMORY_DB_PATH", "data/task_memory.db")
            ),
            task_summary_max_chars=int(os.getenv("AGENT_OS_TASK_SUMMARY_MAX_CHARS", "800")),
        )


def mem0_configured() -> bool:
    return bool(Settings.from_env().mem0_api_key)
