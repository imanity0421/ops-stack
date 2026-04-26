from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_SKILL_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
# 与 agent/skills/loader 一致：可加载子包名
_SKILL_PKG = re.compile(r"^[a-zA-Z0-9_]+$")


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r 不是合法整数，使用默认值 %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning("%s=%r 小于最小值 %s，使用默认值 %s", name, raw, min_value, default)
        return default
    return value


def _env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("%s=%r 不是合法浮点数，使用默认值 %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning("%s=%r 小于最小值 %s，使用默认值 %s", name, raw, min_value, default)
        return default
    return value


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
    #: MemoryController 写入账本，用于跨进程幂等去重与基础审计
    memory_ledger_path: Path = Path("data/memory_ledger.sqlite")
    #: 是否启用 Hindsight 存储与相关工具（默认开启）
    enable_hindsight: bool = True
    #: 是否允许 Agent 写入 Mem0（record_client_*），默认开启；关闭后仍可读取画像（search_client_memory）
    enable_mem0_learning: bool = True
    #: 是否启用参考案例库（Asset Store / LanceDB），默认关闭（未配置时仍可裸跑）
    enable_asset_store: bool = False
    #: 是否启用 Asset Store 候选池的 LLM 加工摘要（默认关闭，避免隐式成本）
    enable_asset_synthesis: bool = False
    #: Asset Store 加工层使用的模型；为空时沿用 AGENT_OS_MODEL / 默认模型
    asset_synthesis_model: str | None = None
    #: 送入 Asset Store LLM 加工层的最大候选数
    asset_synthesis_max_candidates: int = 12
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
    #: Memory Policy 模式：reject 拒写；warn/audit 记录审计后放行
    memory_policy_mode: str = "reject"
    #: 是否在记忆检索渲染中带时间戳
    enable_temporal_grounding: bool = True
    #: 是否启用 Hindsight 候选池的 LLM 加工摘要（默认关闭，避免隐式成本）
    enable_hindsight_synthesis: bool = False
    #: Hindsight 加工层使用的模型；为空时沿用 AGENT_OS_MODEL / 默认模型
    hindsight_synthesis_model: str | None = None
    #: 送入 Hindsight LLM 加工层的最大候选数
    hindsight_synthesis_max_candidates: int = 20
    #: 是否允许 Agent 工具显式输出 Hindsight debug score（默认关闭）
    enable_hindsight_debug_tools: bool = False
    #: 是否启用 Hindsight LanceDB 派生向量候选召回（P3-1 正式 Hybrid Recall，默认关闭）
    enable_hindsight_vector_recall: bool = False
    #: Hindsight LanceDB 派生索引路径；为空时使用 hindsight 文件旁的默认目录
    hindsight_vector_index_path: Path | None = None
    #: Hindsight 向量命中进入最终排序的分数权重
    hindsight_vector_score_weight: float = 6.0
    #: Hindsight 向量召回阶段最多取回的候选行数
    hindsight_vector_candidate_limit: int = 160
    #: 是否启用同一 session 内的 Task-aware Working Memory（首批为 store/prompt 注入）
    enable_task_memory: bool = False
    #: Task-aware Working Memory 本地 SQLite 路径
    task_memory_sqlite_path: Path = Path("data/task_memory.db")
    #: 当前 task summary 的最大建议长度（生成器使用；store 不强截断）
    task_summary_max_chars: int = 800
    #: 当前 task 消息数至少达到该值才生成首个 summary
    task_summary_min_messages: int = 8
    #: 距离上次 summary 新增至少 N 条消息才滚动更新
    task_summary_every_n_messages: int = 6
    #: Task summary 生成模型；为空时沿用 AGENT_OS_MODEL，缺 key 时使用确定性 fallback
    task_summary_model: str | None = None

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
        hist_n = _env_int("AGENT_OS_SESSION_HISTORY_MAX_MESSAGES", 20, min_value=0)

        enable_const = os.getenv("AGENT_OS_ENABLE_CONSTITUTIONAL", "1").lower() not in (
            "0",
            "false",
            "no",
        )
        policy_mode = os.getenv("AGENT_OS_MEMORY_POLICY_MODE", "reject").strip().lower()
        if policy_mode == "audit":
            policy_mode = "warn"
        if policy_mode not in ("reject", "warn"):
            raise ValueError("AGENT_OS_MEMORY_POLICY_MODE 须为 reject、warn 或 audit")

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_api_base=os.getenv("OPENAI_API_BASE"),
            mem0_api_key=os.getenv("MEM0_API_KEY"),
            mem0_host=os.getenv("MEM0_HOST"),
            snapshot_every_n_turns=_env_int("AGENT_OS_SNAPSHOT_EVERY_N_TURNS", 5),
            local_memory_path=Path(
                os.getenv("AGENT_OS_LOCAL_MEMORY_PATH", "data/local_memory.json")
            ),
            hindsight_path=Path(
                os.getenv(
                    "AGENT_OS_HISTORICAL_PATH",
                    os.getenv("AGENT_OS_HISTORICAL_STUB_PATH", "data/hindsight.jsonl"),
                )
            ),
            memory_ledger_path=Path(
                os.getenv("AGENT_OS_MEMORY_LEDGER_PATH", "data/memory_ledger.sqlite")
            ),
            enable_hindsight=os.getenv("AGENT_OS_ENABLE_HINDSIGHT", "1").lower()
            not in ("0", "false", "no"),
            enable_mem0_learning=os.getenv("AGENT_OS_ENABLE_MEM0_LEARNING", "1").lower()
            not in ("0", "false", "no"),
            enable_asset_store=os.getenv("AGENT_OS_ENABLE_ASSET_STORE", "0").lower()
            in ("1", "true", "yes"),
            enable_asset_synthesis=os.getenv("AGENT_OS_ENABLE_ASSET_SYNTHESIS", "0").lower()
            in ("1", "true", "yes"),
            asset_synthesis_model=(os.getenv("AGENT_OS_ASSET_SYNTHESIS_MODEL") or "").strip()
            or None,
            asset_synthesis_max_candidates=_env_int(
                "AGENT_OS_ASSET_SYNTHESIS_MAX_CANDIDATES", 12, min_value=1
            ),
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
            enable_hindsight_synthesis=os.getenv("AGENT_OS_ENABLE_HINDSIGHT_SYNTHESIS", "0").lower()
            in ("1", "true", "yes"),
            hindsight_synthesis_model=(
                os.getenv("AGENT_OS_HINDSIGHT_SYNTHESIS_MODEL") or ""
            ).strip()
            or None,
            hindsight_synthesis_max_candidates=_env_int(
                "AGENT_OS_HINDSIGHT_SYNTHESIS_MAX_CANDIDATES", 20, min_value=1
            ),
            enable_hindsight_debug_tools=(
                os.getenv("AGENT_OS_ENABLE_HINDSIGHT_DEBUG_TOOLS", "0").lower()
                in ("1", "true", "yes")
            ),
            enable_hindsight_vector_recall=(
                os.getenv("AGENT_OS_ENABLE_HINDSIGHT_VECTOR_RECALL", "0").lower()
                in ("1", "true", "yes")
            ),
            hindsight_vector_index_path=Path(hv)
            if (hv := (os.getenv("AGENT_OS_HINDSIGHT_VECTOR_INDEX_PATH") or "").strip())
            else None,
            hindsight_vector_score_weight=_env_float(
                "AGENT_OS_HINDSIGHT_VECTOR_SCORE_WEIGHT", 6.0, min_value=0.0
            ),
            hindsight_vector_candidate_limit=_env_int(
                "AGENT_OS_HINDSIGHT_VECTOR_CANDIDATE_LIMIT", 160, min_value=1
            ),
            enable_task_memory=os.getenv("AGENT_OS_ENABLE_TASK_MEMORY", "0").lower()
            in ("1", "true", "yes"),
            task_memory_sqlite_path=Path(
                os.getenv("AGENT_OS_TASK_MEMORY_DB_PATH", "data/task_memory.db")
            ),
            task_summary_max_chars=_env_int("AGENT_OS_TASK_SUMMARY_MAX_CHARS", 800, min_value=1),
            task_summary_min_messages=_env_int(
                "AGENT_OS_TASK_SUMMARY_MIN_MESSAGES", 8, min_value=1
            ),
            task_summary_every_n_messages=_env_int(
                "AGENT_OS_TASK_SUMMARY_EVERY_N_MESSAGES", 6, min_value=1
            ),
            task_summary_model=(os.getenv("AGENT_OS_TASK_SUMMARY_MODEL") or "").strip() or None,
        )


def mem0_configured() -> bool:
    return bool(Settings.from_env().mem0_api_key)
