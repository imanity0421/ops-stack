# 操作手册（ops-agent）

## 环境

- Python 3.10+
- 网络可访问 OpenAI 或兼容 API（若使用中转，配置 `OPENAI_API_BASE`）

## 安装

```bash
cd ops-agent
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

在 **`ops-agent` 仓库根**（与 `pyproject.toml` 同级）执行一次，把 Git 钩子装上；之后每次 `git commit` 会先跑 **`ruff check --fix`** 与 **`ruff format`**，与 CI 一致、减少漏跑。

```bash
cd ops-agent
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
| `OPS_AGENT_MODEL` | 可选，默认 `gpt-4o-mini` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 可选；设置后启用 Graphiti 只读检索（需 `[graphiti]`） |
| `OPS_KNOWLEDGE_FALLBACK_PATH` | 可选；JSONL 降级，格式见 `docs/examples/knowledge_fallback.example.jsonl` |
| `OPS_HISTORICAL_PATH` | 可选；Hindsight JSONL 路径，默认 `data/hindsight.jsonl`（兼容 `OPS_HISTORICAL_STUB_PATH`） |
| `OPS_ENABLE_HINDSIGHT` | 默认 `1`；设为 `0` 可完全关闭 Hindsight（不创建 store，不挂载相关工具） |
| `OPS_ASYNC_REVIEW_ON_EXIT` | 默认 `1`；设为 `0` 关闭退出时复盘 |
| `OPS_HANDOFF_MANIFEST_PATH` | 可选；`ops-knowledge manifest` 生成的 `handbook_handoff.json`；**运行时**会摘要注入 `get_agent` 指令 |
| `OPS_AGENT_MANIFEST_DIR` | 可选；扫描其中 **`*.json`** 作为 skill 配方（文件名即 **`skill_id`**）；可与包内置 `default_ops` / `short_video` 合并覆盖 |
| `OPS_AGENT_LOADABLE_SKILL_PACKAGES` | 可选；逗号分隔子包名（仅 `[a-zA-Z0-9_]+`），对应 `ops_agent.agent.skills.<name>`；**空**则不加载任何技能包增量工具。测试/示例可设 `toy_skill` |
| `OPS_AGENT_DEFAULT_SKILL_ID` | 可选；未传 `--skill` / `skill_id` 时的默认 skill（默认 `default_ops`） |
| `OPS_GOLDEN_RULES_PATH` | 可选；JSON 数组正则规则（见 `data/golden_rules.example.json`）；启用工具 `check_delivery_text` |
| `OPS_MCP_PROBE_FIXTURE_PATH` | 可选；覆盖默认探针 JSON（否则使用包内 `mcp_probe_default.json`）；工具 `fetch_ops_probe_context` |
| `OPS_ENABLE_ASSET_STORE` | 可选；启用参考案例库（Asset Store / LanceDB），运行时仅检索 |
| `OPS_ASSET_STORE_PATH` | 可选；Asset Store 的本地路径（LanceDB 目录） |
| `OPS_ENABLE_MEM0_LEARNING` | 默认 `1`；设为 `0` 则不挂载 Mem0 写入工具（仍允许检索画像） |
| `OPS_SKILL_COMPLIANCE_DIR` | 可选；目录下 ``<skill_id>.json`` 为硬合规（与 Golden rules 同格式）；**asset-ingest 入库**与工具 **check_skill_compliance_text** 共用 |
| `OPS_ASSET_NEAR_DEDUP_L2_MAX` | 可选；设置如 `0.18` 时启用**近似去重**（特征向量 L2）；不设置则只做强指纹去重 |
| `VIDEO_RAW_INGEST_ROOT` | 可选；供 `doctor` 检查与 `ops-knowledge` 定位 schema |
| `OPS_GRAPHITI_SEARCH_TIMEOUT_SEC` 等 | 见 [ENGINEERING.md](ENGINEERING.md) §5 |
| `OPS_ENABLE_SESSION_DB` | 默认 `1`；`0`/`false`/`no` 关闭 Agno 会话落库（不注入历史、不保留运行记录） |
| `OPS_SESSION_DB_PATH` | 单机 Sqlite 文件路径，默认 `data/agno_session.db`；父目录不存在时会自动创建 |
| `OPS_SESSION_DB_URL` | 可选；**优先于** `OPS_SESSION_DB_PATH`。支持 `sqlite:`、`postgres://`/`postgresql://`、`redis://`/`rediss://`；或**无** `://` 的绝对/相对路径字符串（走 Sqlite 文件） |
| `OPS_SESSION_HISTORY_MAX_MESSAGES` | 将**最近 N 条**历史拼入模型上文；默认 `20`；`0` 表示仍**写入**库但不把历史拼进当轮（适合仅审计） |
| `OPS_ENABLE_CONSTITUTIONAL` | 默认 `1`；`0`/`false`/`no` 关闭系统「宪法」固定段（不推荐生产关闭） |

多 worker / 多机部署时，**不要**在每台机各自写本地 Sqlite，应设 **`OPS_SESSION_DB_URL`** 指向**共享** Postgres 或 Redis（与 Agno 支持的后端一致）。Web 示例中 `/chat` 的 **`session_id`** 须前后端稳定一致（F5 后仍从 `localStorage` 带上；进程重启后可用 `GET /api/session/messages` 拉取转录，见 `examples/web_chat_fastapi.py`）。

**P1 策划类结构化输出**：内置 skill **`planning_draft`**（`--skill planning_draft` 或 Web 传 `skill_id`）在 manifest 中声明 **`output_mode: structured_v1`**，由 Agno 以 Pydantic 强类型输出（见 [ENGINEERING.md](ENGINEERING.md) §3.7）；长文放返回 JSON 的 **`body_markdown`** 字段。

**P2 可观测（Web）**：`examples/web_chat_fastapi.py` 对 **`/chat`** 在请求结束后写 **一条** `OPS_OBS` 前缀日志，含 `request_id`（与头 **`X-Request-ID`** 一致）、`session_id`、`model`、`tools`（分号拼接）、`elapsed_ms`、`tok_in`/`tok_out`/`tok_total`（来自 Agno RunMetrics，作趋势粗算）。grep：`OPS_OBS route=/chat`。

**P2 摄入网关**：**`POST /ingest`**，body 须含显式 **`target`**：`mem0_profile` | `hindsight` | `asset_store`（见 [ingest_post_samples.md](examples/ingest_post_samples.md)）。可选 **`OPS_INGEST_ALLOW_LLM=0`** 在开发时跳过 Asset 入库的 LLM 裁判/抽取（仍做合规与去重）。**生产前**必须在 BFF/网关做 **鉴权 + 限流**（本仓库进程不内置）。

**P3 按 skill 回归**：`pytest -m skill_short_video` / `pytest -m skill_business`（见 [tests/skills/README.md](../tests/skills/README.md)）。

**P3 本地数据备份**：`python scripts/backup_data.py` → `backups/ops_agent_data_*.zip`；Mem0 与恢复说明见 [DATA_BACKUP.md](DATA_BACKUP.md)。

## 环境与依赖自检

```bash
ops-agent doctor
ops-agent doctor --strict
```

## 运行 CLI

```bash
python -m ops-agent --client-id my_client
```

- `--user-id`：多终端用户时区分。
- `--slow`：启用 Agno 内置 `reasoning`；若报错可去掉该 flag（见排障）。
- `--no-knowledge`：不挂载 `search_domain_knowledge`（仅 Mem0）。
- `--skill`：指定 **`skill_id`**（须存在于 manifest 注册表）；影响系统提示与 **Graphiti `graphiti_group_id(client_id, skill)`**。

## 辅助命令（默认数据 / 离线）

```bash
# 端到端规则评测（仅 Golden rules，无 LLM）
ops-agent eval tests/fixtures/e2e_eval_case.json

# 向 JSONL 降级知识库追加行（无需 Neo4j）
ops-agent knowledge-append-jsonl -o data/knowledge.jsonl --client-id my_client --skill default_ops --text "私域复购的关键是..."

# Graphiti 离线写入：先 dry-run，再在有 NEO4J_* + OPENAI_API_KEY 时实跑
ops-agent graphiti-ingest docs/examples/graphiti_episodes.example.json --dry-run

# 参考案例库：离线导入（需 pip install -e ".[asset_store]"，含 lancedb）
# ops-agent asset-ingest my_case.txt --client-id my_client --skill short_video
# 删除单条或按 tenant+skill 清空（回退垃圾入库）
# ops-agent asset-rm --case-id <uuid>
# ops-agent asset-rm --client-id my_client --skill short_video --all-skill

# MCP 探针 stdio 服务（需 pip install -e ".[mcp]"）
ops-agent mcp-probe-server
```

## 产出文件（本地模式）

| 路径 | 说明 |
|------|------|
| `data/local_memory.json` | 无 Mem0 时的本地记忆 |
| `data/hindsight.jsonl`（`OPS_HISTORICAL_PATH`） | Hindsight：反馈与复盘教训，JSONL |

## 排障

1. **`ImportError` / Agno API 变化**  
   对照 <https://docs.agno.com>，仅修改 `ops_agent/agent/factory.py` 中的构造逻辑。

2. **Mem0 鉴权失败**  
   检查 `MEM0_API_KEY`；或暂时去掉该变量使用本地 JSON。

3. **`reasoning` 相关错误**  
   不使用 `--slow`，或更换支持推理链的模型（以 Agno 文档为准）。

4. **Windows 控制台中文乱码**  
   可设置环境变量 `PYTHONIOENCODING=utf-8`，或使用 Windows Terminal UTF-8 代码页。

5. **`graphiti-core` 未安装**  
   执行 `pip install -e ".[graphiti]"`。

6. **Graphiti 连接失败**  
   检查 Neo4j 是否可达、`group_id` 与入库时是否一致（**`graphiti_group_id(client_id, skill_id)`**）；可临时配置 `OPS_KNOWLEDGE_FALLBACK_PATH` 仅测 Agent 流程。

7. **AsyncReview**  
   退出 CLI 时默认会复盘并写入 `Hindsight` 教训（需 `OPENAI_API_KEY`）。可用 `--no-async-review` 或 `OPS_ASYNC_REVIEW_ON_EXIT=0` 关闭。

## 作为库导入

```python
from ops_agent.config import Settings
from ops_agent.memory.controller import MemoryController
from ops_agent.agent.factory import get_agent
from ops_agent.knowledge.graphiti_reader import GraphitiReadService

settings = Settings.from_env()
ctrl = MemoryController.create_default(
    mem0_api_key=settings.mem0_api_key,
    mem0_host=settings.mem0_host,
    local_memory_path=settings.local_memory_path,
    hindsight_path=settings.hindsight_path,
)
knowledge = GraphitiReadService.from_env(settings.knowledge_fallback_path)
agent = get_agent(
    ctrl,
    client_id="c1",
    user_id="u1",
    thought_mode="fast",
    knowledge=knowledge,
    skill_id=None,  # 默认 Settings.default_skill_id；Graphiti 使用 graphiti_group_id(c1, skill)
)
```
