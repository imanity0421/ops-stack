# 操作手册（agent-os-runtime）

## 环境

- Python 3.10+
- 网络可访问 OpenAI 或兼容 API（若使用中转，配置 `OPENAI_API_BASE`）

## 安装

```bash
cd agent-os-runtime
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -U pip
# 全量单测、Ruff 检查与无 skip 的 Asset 用例需 dev（pytest、ruff、lancedb）：
pip install -e ".[dev]"
# 仅运行核心包、接受部分用例因缺 lancedb 失败时，可 `pip install -e .`（不推荐，与 CI 不一致）
# 领域知识（Graphiti，可选）
pip install -e ".[graphiti]"
```

## 开发：pre-commit（提交前跑 Ruff）

在 **`agent-os-runtime` 仓库根**（与 `pyproject.toml` 同级）执行一次，把 Git 钩子装上；之后每次 `git commit` 会先跑 **`ruff check --fix`** 与 **`ruff format`**，与 CI 一致、减少漏跑。

```bash
cd agent-os-runtime
pre-commit install
# 若 `pre-commit` 不在 PATH（常见于仅当前 venv）：`python -m pre_commit install`
# 可选：对当前树全量验一遍
pre-commit run --all-files
# 同上可：`python -m pre_commit run --all-files`
```

未装钩子时，也可手动：``ruff check src tests``、``ruff format --check src tests``（或 ``ruff format src tests`` 写回）。

## 配置

复制 `.env.example` 为 `.env`，至少设置：

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | 必需 |
| `OPENAI_API_BASE` | 可选，OpenAI 兼容端点 |
| `MEM0_API_KEY` | 可选；未设置则使用 `data/local_memory.json` |
| `MEM0_HOST` | 可选，默认 Mem0 云 |
| `AGENT_OS_MODEL` | 可选，默认 `gpt-4o-mini` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 可选；设置后启用 Graphiti 只读检索（需 `[graphiti]`） |
| `AGENT_OS_KNOWLEDGE_FALLBACK_PATH` | 可选；JSONL 降级，格式见 `docs/examples/knowledge_fallback.example.jsonl` |
| `AGENT_OS_HISTORICAL_PATH` | 可选；Hindsight JSONL 路径，默认 `data/hindsight.jsonl`（兼容 `AGENT_OS_HISTORICAL_STUB_PATH`） |
| `AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE` | 默认 `1`；`0`/`false`/`no` 时关闭 Hindsight **同类合并与频次加分**（**仍**对 `supersedes_event_id` 命中行做**召回降权**）；见 [MEMORY_SYSTEM_V2.md](MEMORY_SYSTEM_V2.md) §Hindsight |
| `AGENT_OS_ENABLE_HINDSIGHT` | 默认 `1`；设为 `0` 可完全关闭 Hindsight（不创建 store，不挂载相关工具） |
| `AGENT_OS_ASYNC_REVIEW_ON_EXIT` | 默认 `1`；设为 `0` 关闭退出时复盘 |
| `AGENT_OS_HANDOFF_MANIFEST_PATH` | 可选；`ops-knowledge manifest` 生成的 `handbook_handoff.json`；**运行时**会摘要注入 `get_agent` 指令 |
| `AGENT_OS_MANIFEST_DIR` | 可选；扫描其中 **`*.json`** 作为 skill 配方（文件名即 **`skill_id`**）；可覆盖包内置 `default_agent` 或增补外部 skill |
| `AGENT_OS_LOADABLE_SKILL_PACKAGES` | 可选；逗号分隔子包名（仅 `[a-zA-Z0-9_]+`），对应 `agent_os.agent.skills.<name>`；**空**则不加载任何技能包增量工具。测试/示例可设 `sample_skill` |
| `AGENT_OS_DEFAULT_SKILL_ID` | 可选；未传 `--skill` / `skill_id` 时的默认 skill（默认 `default_agent`） |
| `AGENT_OS_GOLDEN_RULES_PATH` | 可选；JSON 数组正则规则（见 `data/golden_rules.example.json`）；启用工具 `check_delivery_text` |
| `AGENT_OS_MCP_PROBE_FIXTURE_PATH` | 可选；覆盖默认探针 JSON（否则使用包内 `mcp_probe_default.json`）；工具 `fetch_probe_context` |
| `AGENT_OS_ENABLE_ASSET_STORE` | 可选；启用参考案例库（Asset Store / LanceDB），运行时仅检索 |
| `AGENT_OS_ASSET_STORE_PATH` | 可选；Asset Store 的本地路径（LanceDB 目录） |
| `AGENT_OS_ENABLE_MEM0_LEARNING` | 默认 `1`；设为 `0` 则不挂载 Mem0 写入工具（仍允许检索画像） |
| `AGENT_OS_SKILL_COMPLIANCE_DIR` | 可选；目录下 ``<skill_id>.json`` 为硬合规（与 Golden rules 同格式）；**asset-ingest 入库**与工具 **check_skill_compliance_text** 共用 |
| `AGENT_OS_ASSET_NEAR_DEDUP_L2_MAX` | 可选；设置如 `0.18` 时启用**近似去重**（特征向量 L2）；不设置则只做强指纹去重 |
| `VIDEO_RAW_INGEST_ROOT` | 可选；供 `doctor` 检查与 `ops-knowledge` 定位 schema |
| `AGENT_OS_GRAPHITI_SEARCH_TIMEOUT_SEC` 等 | 见 [ENGINEERING.md](ENGINEERING.md) §5 |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH` | Graphiti 权限持久化 JSON 路径（默认 `data/graphiti_entitlements.json`）；文件优先，env（`AGENT_OS_GRAPHITI_ALLOWED_SKILL_IDS` / `AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON`）兜底 |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH` | Graphiti 权限变更审计日志 JSONL 路径（默认 `data/graphiti_entitlements_audit.jsonl`） |
| `AGENT_OS_ENABLE_SESSION_DB` | 默认 `1`；`0`/`false`/`no` 关闭 Agno 会话落库（不注入历史、不保留运行记录） |
| `AGENT_OS_SESSION_DB_PATH` | 单机 Sqlite 文件路径，默认 `data/agno_session.db`；父目录不存在时会自动创建 |
| `AGENT_OS_SESSION_DB_URL` | 可选；**优先于** `AGENT_OS_SESSION_DB_PATH`。支持 `sqlite:`、`postgres://`/`postgresql://`、`redis://`/`rediss://`；或**无** `://` 的绝对/相对路径字符串（走 Sqlite 文件） |
| `AGENT_OS_SESSION_HISTORY_MAX_MESSAGES` | 将**最近 N 条**历史拼入模型上文；默认 `20`；`0` 表示仍**写入**库但不把历史拼进当轮（适合仅审计） |
| `AGENT_OS_ENABLE_CONSTITUTIONAL` | 默认 `1`；`0`/`false`/`no` 关闭系统「宪法」固定段（不推荐生产关闭） |
| `AGENT_OS_ENABLE_EPHEMERAL_METADATA` | 默认 `1`；每轮 prompt 注入当前时间、入口、skill 等**临时上下文**，不写长期记忆 |
| `AGENT_OS_TIMEZONE` | 临时上下文的本地时区，默认 `Asia/Shanghai` |
| `AGENT_OS_ENABLE_MEMORY_POLICY` | 默认 `1`；在 `MemoryController` 侧拒绝玩笑、临时、模糊内容写入长期记忆 |
| `AGENT_OS_MEMORY_POLICY_MODE` | `reject`（默认，拒写）或 `warn`（仅日志告警后放行） |
| `AGENT_OS_ENABLE_TEMPORAL_GROUNDING` | 默认 `1`；检索结果渲染带 `[记录于 ...]`，帮助模型区分新旧资料 |
| `AGENT_OS_ENABLE_TASK_MEMORY` | 默认 `0`；启用同一 session 内的 Task-aware Working Memory（自动 `task_id` + 当前 task summary） |
| `AGENT_OS_TASK_MEMORY_DB_PATH` | Task-aware Working Memory 的本地 SQLite 路径，默认 `data/task_memory.db` |
| `AGENT_OS_TASK_SUMMARY_MAX_CHARS` | 当前 task summary 建议最大长度，默认 `800` |

多 worker / 多机部署时，**不要**在每台机各自写本地 Sqlite，应设 **`AGENT_OS_SESSION_DB_URL`** 指向**共享** Postgres 或 Redis（与 Agno 支持的后端一致）。Web 示例中 `/chat` 的 **`session_id`** 须前后端稳定一致（F5 后仍从 `localStorage` 带上；进程重启后可用 `GET /api/session/messages` 拉取转录，见 `examples/web_chat_fastapi.py`）。

**P1 结构化输出**：内置 skill **`planning_draft`**（`--skill planning_draft` 或 Web 传 `skill_id`）在 manifest 中声明 **`output_mode: structured_v1`**，由 Agno 以 Pydantic 强类型输出（见 [ENGINEERING.md](ENGINEERING.md) §3.7）；长文放返回 JSON 的 **`body_markdown`** 字段。

**P2 可观测（Web）**：`examples/web_chat_fastapi.py` 对 **`/chat`** 在请求结束后写 **一条** `AGENT_OS_OBS` 前缀日志，含 `request_id`（与头 **`X-Request-ID`** 一致）、`session_id`、`model`、`tools`（分号拼接）、`elapsed_ms`、`tok_in`/`tok_out`/`tok_total`（来自 Agno RunMetrics，作趋势粗算）。grep：`AGENT_OS_OBS route=/chat`。

**P2 摄入网关**：**`POST /ingest`**，body 须含显式 **`target`**：`mem0_profile` | `hindsight` | `asset_store`（见 [ingest_post_samples.md](examples/ingest_post_samples.md)）。可选 **`AGENT_OS_INGEST_ALLOW_LLM=0`** 在开发时跳过 Asset 入库的 LLM 裁判/抽取（仍做合规与去重）。**生产前**必须在 BFF/网关做 **鉴权 + 限流**（本仓库进程不内置）。
**Graphiti 权限持久化**：默认读取 `data/graphiti_entitlements.json`（可由 `AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH` 覆盖），授权判定为**文件优先、env 兜底**。可用 `agent-os-runtime graphiti-entitlements` 管理；`doctor` 会校验该文件 JSON 结构与字段类型。

**按 skill 回归**：核心仓只保留 `tests/core/`。外部 skill pack 可在自己的仓库或 `tests/skill_examples/` 下定义独立 fixtures 与 marker。

**P3 本地数据备份**：`python scripts/backup_data.py` → `backups/agent_os_data_*.zip`；Mem0 与恢复说明见 [DATA_BACKUP.md](DATA_BACKUP.md)。

## 环境与依赖自检

```bash
agent-os-runtime doctor
agent-os-runtime doctor --strict
```

## 运行 CLI

```bash
agent-os-runtime --client-id my_client
# 或在源码树未安装包时：
PYTHONPATH=src python -m agent_os --client-id my_client
```

- `--user-id`：多终端用户时区分。
- `--slow`：启用 Agno 内置 `reasoning`；若报错可去掉该 flag（见排障）。
- `--no-knowledge`：不挂载 `search_domain_knowledge`（仅 Mem0）。
- `--skill`：指定 **`skill_id`**（须存在于 manifest 注册表）；影响系统提示与 **Graphiti 分区**：新语义下写入/优先检索为 **`system_graphiti_group_id(skill_id)`**；仍兼容只读旧分区 **`graphiti_group_id(client_id, skill_id)`**（可用 `AGENT_OS_GRAPHITI_ENABLE_LEGACY_CLIENT_GROUPS=0` 关闭，见下文「Memory V2」）。

## 辅助命令（默认数据 / 离线）

```bash
# 端到端规则评测（仅 Golden rules，无 LLM）
agent-os-runtime eval tests/core/fixtures/e2e_eval_case.json

# 向 JSONL 降级知识库追加行（无需 Neo4j）
agent-os-runtime knowledge-append-jsonl -o data/knowledge.jsonl --client-id my_client --skill default_agent --text "通用交付流程需先确认目标与约束..."

# Graphiti 离线写入：先 dry-run，再在有 NEO4J_* + OPENAI_API_KEY 时实跑
agent-os-runtime graphiti-ingest docs/examples/graphiti_episodes.example.json --dry-run

# Graphiti 权限持久化：查看 / 设置（默认 data/graphiti_entitlements.json）
agent-os-runtime graphiti-entitlements --show
agent-os-runtime graphiti-entitlements --set-global "default_agent,short_video"
agent-os-runtime graphiti-entitlements --client-id acme --set-client "short_video"
agent-os-runtime graphiti-entitlements --client-id acme --remove-client
# 乐观并发控制：仅当 revision 匹配时写入（不匹配返回退出码 2）
agent-os-runtime graphiti-entitlements --set-global "default_agent" --expected-revision 3
# 可选：为 CLI 审计指定操作者名（否则回退 USERNAME/USER）
# AGENT_OS_ACTOR=ops_admin

# 参考案例库：离线导入（需 pip install -e ".[asset_store]"，含 lancedb）
# agent-os-runtime asset-ingest my_case.txt --client-id my_client --skill default_agent
# 删除单条或按 tenant+skill 清空（回退垃圾入库）
# agent-os-runtime asset-rm --case-id <uuid>
# agent-os-runtime asset-rm --client-id my_client --skill default_agent --all-skill

# MCP 探针 stdio 服务（需 pip install -e ".[mcp]"）
agent-os-runtime mcp-probe-server
```

## 产出文件（本地模式）

| 路径 | 说明 |
|------|------|
| `data/local_memory.json` | 无 Mem0 时的本地记忆 |
| `data/hindsight.jsonl`（`AGENT_OS_HISTORICAL_PATH`） | Hindsight：反馈与复盘教训，JSONL |

## Memory V2 运维

设计背景见 [MEMORY_SYSTEM_V2.md](MEMORY_SYSTEM_V2.md)。本节列出**运维最常碰到的环境变量**与**离线迁移命令**。

### 检索编排（库 / 工具）

- 工具 **`retrieve_ordered_context`** 的四层顺序（Mem0 → Hindsight → Graphiti → Asset）由 **`MemoryController.retrieve_ordered_context`** 与 **`agent_os.memory.ordered_context`** 实现；在业务代码中集成时，应优先调用控制器方法，避免复制工具内的 Markdown 拼接逻辑。

### Mem0：公司共享桶与双路召回

- 新写入的公司级画像/事实应使用保留用户 **`__client_shared__`**（见 `CLIENT_SHARED_USER_ID`），不再用 `user_id=None` 表示共享。
- 兼容：控制器仍会对旧 **`user_id=None`** 的本地桶做读取（与 `migrate_memory_v2` 归一化互补）。

### Graphiti：系统分区与只读 legacy

本节只覆盖 Graphiti 作为 Memory V2 第三层知识检索时需要的运行配置。当前权限持久化主路径是本地 JSON 文件；SQLite/Postgres 后端已从当前阶段回退，避免把 Memory V2 拖入权限平台建设。

| 变量 | 说明 |
|------|------|
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH` | 权限持久化 JSON 路径；默认 `data/graphiti_entitlements.json`（示例见 `docs/examples/graphiti_entitlements.example.json`） |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH` | 权限变更审计日志 JSONL 路径；默认 `data/graphiti_entitlements_audit.jsonl` |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_CACHE_TTL_SEC` | 权限缓存 TTL 秒数（默认 `2`）；同时会在文件 mtime/env 变化时立即重载 |
| `AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC` | 权限文件与审计日志写入获取锁的超时秒数（默认 `5`） |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_BYTES` | 审计日志单文件滚动阈值（字节，默认 `2097152`） |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_FILES` | 审计日志最大滚动文件数（默认 `10`，文件名 `.1`~`.N`） |
| `AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_RETENTION_DAYS` | 审计日志保留天数（默认 `30`，过期文件在写入时清理） |
| `AGENT_OS_GRAPHITI_ALLOWED_SKILL_IDS` | 可选；逗号分隔，全局允许的 skill/domain（与 `graphiti_reader` 权限模型一致） |
| `AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON` | 可选；JSON，如 `{"client_a":["skill1","skill2"]}`，按 client 限制可检索的 skill |
| `AGENT_OS_GRAPHITI_ENABLE_LEGACY_CLIENT_GROUPS` | 默认 `1`；`0`/`false`/`no` 时**不再**在系统分区无结果时回退旧 `client__skill` 分区 |

超时、BFS 深度、最大条数等仍见 [ENGINEERING.md](ENGINEERING.md) §5。
Web 内网管理（可选）：`examples/web_chat_fastapi.py` 提供 `/api/admin/graphiti-entitlements*`；需 `AGENT_OS_WEB_ENABLE_ADMIN_API=1`、并配置 `AGENT_OS_WEB_ADMIN_API_TOKEN`（或 `AGENT_OS_WEB_ADMIN_API_TOKENS`），且仅接受 `AGENT_OS_WEB_ADMIN_ALLOWED_HOSTS` 白名单来源访问。请求头可用 `x-admin-token` 或 `Authorization: Bearer <token>`；可选 `x-admin-actor` 用于审计落库。写操作支持 `expected_revision`，冲突返回 HTTP `409`（含 `expected_revision` / `actual_revision` 与重试提示）。此外支持 `Idempotency-Key` 幂等去重（`AGENT_OS_WEB_ADMIN_IDEMPOTENCY_ENABLED`，默认开启；TTL 见 `AGENT_OS_WEB_ADMIN_IDEMPOTENCY_TTL_SEC`）。

如果环境中仍设置了历史变量 `AGENT_OS_GRAPHITI_ENTITLEMENTS_STORE=sqlite/postgres`，运行时会告警并继续使用文件后端。

### 可选 LLM 加工层（成本开关）

| 变量 | 说明 |
|------|------|
| `AGENT_OS_ENABLE_HINDSIGHT_SYNTHESIS` | 默认 `0`；`1` 时对 Hindsight 候选池做 LLM 摘要/去重风格加工 |
| `AGENT_OS_HINDSIGHT_SYNTHESIS_MODEL` | 可选；不填则沿用 `AGENT_OS_MODEL` |
| `AGENT_OS_HINDSIGHT_SYNTHESIS_MAX_CANDIDATES` | 默认 `20` |
| `AGENT_OS_ENABLE_ASSET_SYNTHESIS` | 默认 `0`；`1` 时对 Asset Store 候选池做 LLM 加工 |
| `AGENT_OS_ASSET_SYNTHESIS_MODEL` | 可选 |
| `AGENT_OS_ASSET_SYNTHESIS_MAX_CANDIDATES` | 默认 `12` |

### 离线数据迁移（`scripts/migrate_memory_v2.py`）

在 **`agent-os-runtime` 根目录**、已 `pip install -e ".[dev]"`（或至少能 import `agent_os`）的前提下：

**1）本地 Mem0 JSON（`MEM0_API_KEY` 未设置时）**

将旧版仅租户根键 `users[<client_id>]` 的记忆合并进 **`users[<client_id>::__client_shared__]`**，并删除旧键（幂等：已只有新键时几乎无操作）。

```bash
python scripts/migrate_memory_v2.py local-memory --path data/local_memory.json --dry-run
python scripts/migrate_memory_v2.py local-memory --path data/local_memory.json
```

**2）Graphiti JSONL fallback（`AGENT_OS_KNOWLEDGE_FALLBACK_PATH` 指向的文件）**

把旧 **`graphiti_group_id(client, skill)`** 行迁移为 **`system_graphiti_group_id(skill)`**：

- **`--mode duplicate`**（默认）：保留原行，再追加一行新 `group_id`（最安全，磁盘加倍）。
- **`--mode replace`**：原地替换 `group_id`（确认无依赖旧键后再用）。

```bash
python scripts/migrate_memory_v2.py knowledge-jsonl --path data/knowledge.jsonl --mode duplicate --dry-run
python scripts/migrate_memory_v2.py knowledge-jsonl --path data/knowledge.jsonl --mode duplicate
```

迁移前建议：**备份**（见上文 `scripts/backup_data.py` / [DATA_BACKUP.md](DATA_BACKUP.md)）、**先 dry-run**、在维护窗口执行。Mem0 **云端**桶归一需走 Mem0 官方能力或自建脚本，本仓库脚本仅覆盖**本地 JSON** 文件。

## 排障

1. **`ImportError` / Agno API 变化**  
   对照 <https://docs.agno.com>，仅修改 `agent_os/agent/factory.py` 中的构造逻辑。

2. **Mem0 鉴权失败**  
   检查 `MEM0_API_KEY`；或暂时去掉该变量使用本地 JSON。

3. **`reasoning` 相关错误**  
   不使用 `--slow`，或更换支持推理链的模型（以 Agno 文档为准）。

4. **Windows 控制台中文乱码**  
   可设置环境变量 `PYTHONIOENCODING=utf-8`，或使用 Windows Terminal UTF-8 代码页。

5. **`graphiti-core` 未安装**  
   执行 `pip install -e ".[graphiti]"`。

6. **Graphiti 连接失败**  
   检查 Neo4j 是否可达；新入库与优先检索使用 **`system_graphiti_group_id(skill_id)`**；历史数据可能仍在 **`graphiti_group_id(client_id, skill_id)`**，运行时默认只读兼容（见 `AGENT_OS_GRAPHITI_ENABLE_LEGACY_CLIENT_GROUPS`）。可临时配置 `AGENT_OS_KNOWLEDGE_FALLBACK_PATH` 仅测 Agent 流程。

7. **AsyncReview**  
   退出 CLI 时默认会复盘并写入 `Hindsight` 教训（需 `OPENAI_API_KEY`）。可用 `--no-async-review` 或 `AGENT_OS_ASYNC_REVIEW_ON_EXIT=0` 关闭。

## 作为库导入

```python
from agent_os.config import Settings
from agent_os.memory.controller import MemoryController
from agent_os.memory.ordered_context import RetrieveOrderedContextOptions
from agent_os.agent.factory import get_agent
from agent_os.knowledge.graphiti_reader import GraphitiReadService

settings = Settings.from_env()
ctrl = MemoryController.create_default(
    mem0_api_key=settings.mem0_api_key,
    mem0_host=settings.mem0_host,
    local_memory_path=settings.local_memory_path,
    hindsight_path=settings.hindsight_path,
)
knowledge = GraphitiReadService.from_env(settings.knowledge_fallback_path)
skill = settings.default_skill_id
opts = RetrieveOrderedContextOptions(
    client_id="c1",
    user_id="u1",
    skill_id=skill,
    enable_hindsight=settings.enable_hindsight,
    enable_temporal_grounding=settings.enable_temporal_grounding,
    knowledge=knowledge,
    enable_asset_store=settings.enable_asset_store,
    asset_store=None,
    enable_hindsight_synthesis=settings.enable_hindsight_synthesis,
    hindsight_synthesis_model=settings.hindsight_synthesis_model,
    hindsight_synthesis_max_candidates=settings.hindsight_synthesis_max_candidates,
    enable_asset_synthesis=settings.enable_asset_synthesis,
    asset_synthesis_model=settings.asset_synthesis_model,
    asset_synthesis_max_candidates=settings.asset_synthesis_max_candidates,
)
markdown_context = ctrl.retrieve_ordered_context("本轮用户问题摘要", opts)
agent = get_agent(
    ctrl,
    client_id="c1",
    user_id="u1",
    thought_mode="fast",
    knowledge=knowledge,
    skill_id=None,  # 默认 Settings.default_skill_id；Graphiti 优先 system_graphiti_group_id(skill)，legacy 分区可选只读兼容
)
```
