# 变更日志

格式基于实际交付，版本号与 `pyproject.toml` / `agent_os.__version__` 对齐。

## [Unreleased]

### 文档

- **文档权威层级整理**：明确 [CLAUDE_CODE_REFERENCE_ROADMAP.md](CLAUDE_CODE_REFERENCE_ROADMAP.md) 为业务 / Claude Code Harness 参考架构中心文档；Sprint/DoD 执行拆解改为 [SPRINT_IMPLEMENTATION_ROADMAP.md](SPRINT_IMPLEMENTATION_ROADMAP.md)，并同步 README、ENGINEERING、Memory/Context/Stage 文档的关系说明与链接。
- **文档入口合并**：原 `docs/README.md` 已撤并至仓库 [README.md](../README.md) §**文档与阅读顺序**（避免根目录与 `docs/` 双 README）；[AGENTS.md](../AGENTS.md) 与相关交叉链接已更新。
- 统一 **Hindsight `supersedes_event_id`** 表述：**append-only 存储，召回层降权**（与 `HindsightRetrievalPolicy` 一致），修正 [ENGINEERING.md](ENGINEERING.md)、[OPERATIONS.md](OPERATIONS.md)、[MEMORY_SYSTEM_V2.md](MEMORY_SYSTEM_V2.md)、[examples/ingest_post_samples.md](examples/ingest_post_samples.md) 中旧版「从召回剔除/隐藏」等措辞。

### 改进

- **Memory V2 编排**：`retrieve_ordered_context` 四层 Markdown 组装收敛至 `MemoryController.retrieve_ordered_context` + `agent_os.memory.ordered_context`；Mem0/Hindsight/Asset 块格式化提取到 `agent_os.memory.context_formatters`。
- **Memory V2 验收测试**：补齐四层完整召回、Agent 工具入口、Graphiti legacy 开关、Hindsight 租户隔离、Asset scope 可见性与 Mem0 V2 metadata 落盘的最小验收覆盖。
- **Mem0 检索治理**：`search_profile` 跨桶合并时对**相同正文**保留 `recorded_at`/`created_at` 更晚的命中；`ENGINEERING.md` 同步 Graphiti 系统分区 + legacy 只读、`AGENT_OS_GRAPHITI_*` 索引。
- **Hindsight 频次 / 合并 / supersedes**：JSONL 支持 `supersedes_event_id`、`weight_count`；`search_lessons` 对被 supersedes 的事件在排序中**降权**（append-only，不删行）、按规范化正文合并桶并展示「同类×n，总权重×w」、对数频次加分；`AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE` 可关合并（仍保留 supersedes **降权**计分）；`UserFact` 增加对应字段。
- **摄入 / 工具透传**：`run_ingest_v1` 与 Web `POST /ingest`（`IngestV1In`）在 `target=hindsight` 时支持 `supersedes_event_id`、`weight_count`；工具 **`record_task_feedback`** 同步可选参数。
- **Graphiti 权限持久化**：新增 `data/graphiti_entitlements.json` 语义与 `GraphitiEntitlements` 解析器；`search_domain_knowledge` 改为“文件优先、env 兜底”；CLI 新增 `graphiti-entitlements`（show/set/remove）。
- **Graphiti 运维补强**：新增 `docs/examples/graphiti_entitlements.example.json`；`doctor` 增加权限文件结构与字段类型校验；Web 增加可选内网管理接口 `/api/admin/graphiti-entitlements*`（默认关闭，且限制本机访问）。
- **Graphiti 管理安全与审计**：Web 管理接口增加 token 鉴权（`AGENT_OS_WEB_ADMIN_API_TOKEN(S)` + `x-admin-token`/Bearer），CLI 与 Web 的 entitlements 变更统一写入审计 JSONL（`AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH`）。
- **Graphiti 权限热加载**：新增 `GraphitiEntitlementsProvider`，支持缓存 TTL（`AGENT_OS_GRAPHITI_ENTITLEMENTS_CACHE_TTL_SEC`）并在权限文件 mtime / env 变化时自动失效重载；`GraphitiReadService` 可手动 `invalidate_entitlements_cache()`。
- **Graphiti 并发写保护**：权限文件写入改为锁文件互斥 + 原子替换（`os.replace`），审计日志 append 加锁；新增 `AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC` 与并发测试覆盖。
- **Graphiti 乐观并发控制**：权限文档新增 `revision`；CLI `graphiti-entitlements` 与 Web 管理写接口支持 `expected_revision` 冲突检测（CLI 冲突返回码 `2`，Web 返回 `409`），防止“最后写入覆盖”。
- **冲突提示改进**：权限写冲突时返回 `expected/actual revision` 与重试提示（CLI stderr 与 Web 409 detail 结构化字段）。
- **审计日志运维策略**：Graphiti 权限审计支持滚动切分与保留期（`AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_BYTES` / `..._MAX_FILES` / `..._RETENTION_DAYS`）。
- **Web 管理接口测试**：新增 `tests/core/test_web_admin_api.py` 覆盖鉴权成功/失败、revision 冲突、审计落库链路。
- **Web 管理接口幂等键**：支持 `Idempotency-Key`（同键同请求体返回缓存结果、不同请求体复用同键返回 `409`），减少网络重试导致的重复写与重复审计。
- **Graphiti 权限后端收缩**：回退 `sqlite` / `postgres` entitlements 后端，当前阶段仅保留文件权限模型，避免 Memory V2 被权限平台化工作牵引。
- **工程卫生**：模块 docstring 置于文件首（符合 PEP 236 + Ruff E402）；`observability` 在顶层 token 为 0 时聚合 ``RunMetrics.details``；Web ``/chat`` 增加 ``reply_content_kind`` / ``structured`` 以支持 ``planning_draft`` 等结构化输出；**可选依赖** ``dev`` 含 ``lancedb`` 便于全量测试。
- **工程卫生（继续）**：``pyproject.toml`` 增加 ``[tool.ruff]``；CI 增加 ``ruff format --check``；全仓已 ``ruff format`` 对齐；Asset 入库单测改为显式 ``import lancedb``（缺依赖时失败而非 skip，与 ``.[dev]`` 一致）；``docs/OPERATIONS.md`` 默认安装改为 ``.[dev]`` 以匹配 CI。
- **Pre-commit**：仓库根 ``.pre-commit-config.yaml``（``ruff-check`` + ``ruff-format``）；``.[dev]`` 含 ``pre-commit``；见 ``docs/OPERATIONS.md`` 启用 ``pre-commit install``。

### 新增

- **P1.5 认知稳定性增强路线**：记录 Ephemeral Metadata、Memory Policy、Temporal Grounding、Session Summarizer 的设计路线；Forge 程序性记忆仅作为 `pending_review` 候选 SOP 待办，禁止自动写入干净知识库。
- **P1.5 认知稳定性增强实现（首批）**：`runtime_context` 注入临时时间/入口/skill；`Memory Policy` 服务端 gate 拒绝脏记忆写入；Mem0/Hindsight/Asset 检索渲染增加时间线提示；新增 `AGENT_OS_ENABLE_EPHEMERAL_METADATA` / `AGENT_OS_ENABLE_MEMORY_POLICY` / `AGENT_OS_ENABLE_TEMPORAL_GROUNDING` 等配置。
- **Task-aware Working Memory 设计**：将 Session Summarizer 收敛为同一 session 内的 `task_id` 自动管理 + task summary；采用保守边界检测、candidate/confirmed、有限回溯与 audit，跨 session 信息仍由 Mem0/Hindsight 承担。
- **Task-aware Working Memory 首批实现**：新增 `agent.task_memory`（SQLite task/message/summary/audit schema、`task_id` 自动生成、summary/index prompt helper）；`get_agent` 支持注入当前 task summary 与短 task index；新增 `AGENT_OS_ENABLE_TASK_MEMORY` 等配置。
- **Sprint 4 P3-7 Skill 评测协议**：核心仓保留 `tests/core/`；外部 skill pack 可自带 fixtures 与 pytest markers；仍统一 `run_e2e_eval_*` 引擎。
- **Sprint 4 P3-8 数据运维**：`agent_os.backup_data_core`、`scripts/backup_data.py` 生成本地 `data/` 候选文件 zip；`docs/DATA_BACKUP.md`（含 Mem0 官方/人工 SOP）；`backups/` 已加入 `.gitignore`。
- **Sprint 3 P2-5 可观测性**：`agent_os.observability`（`AGENT_OS_OBS` 日志行）；Web 示例中间件透传 **`X-Request-ID`**；**`/chat`** 结束打 `session_id` / `model` / `tools` / `elapsed_ms` / token 粗算。
- **Sprint 3 P2-6 数据摄入网关**：`agent_os.ingest_gateway.run_ingest_v1` + **`POST /ingest`**（`target=mem0_profile|hindsight|asset_store`）；样例见 `docs/examples/ingest_post_samples.md`；可选 **`AGENT_OS_INGEST_ALLOW_LLM`**。
- **Sprint 2 P1-3 系统宪法**：`agent_os.agent.constitutional` 固定「冲突解决序」并置于 `get_agent` 指令最前；`AGENT_OS_ENABLE_CONSTITUTIONAL`；`AgentManifestV1.constitutional_prompt` 可选补充。`retrieve_ordered_context` 说明与宪法互补。验收表见 `docs/examples/constitutional_test_cases.md`。
- **Sprint 2 P1-4 交付物契约**：`manifest.output_mode`=`structured_v1` + `output_schema_version`=`1.0` → Agno `output_schema`=`PlanStructuredV1`（`body_markdown` 承载长文）；内置 skill **`planning_draft`**。
- **Sprint 1 P0-2 会话持久化**：`config.Settings` 增加 `enable_session_db`、`session_sqlite_path`、`session_db_url`、`session_history_max_messages`；`agent_os.agent.session_db.create_session_db` 供 `get_agent` 挂接 Agno `db`；默认注入最近 N 条历史。Web 增加 `GET /api/session/messages` 供重启后拉取与模型一致的历史。

### 文档

- **Memory V2 运维**：`docs/OPERATIONS.md` 增补「Memory V2 运维」（环境变量、Graphiti legacy、`migrate_memory_v2`）；`MEMORY_SYSTEM_V2.md` 增补运维与迁移交叉说明。
- **Sprint 实施路线图**：新增 [docs/SPRINT_IMPLEMENTATION_ROADMAP.md](SPRINT_IMPLEMENTATION_ROADMAP.md)（Sprint 1–4、DoD、Mermaid 设计图与实现落点）；`ENGINEERING.md` §7.1 引用。

### 新增

- **Sprint 1 P0-1 Skill 扩展协议**：`agent_os.agent.skills.loader` 白名单动态加载 `agent_os.agent.skills.<name>`；环境变量 **`AGENT_OS_LOADABLE_SKILL_PACKAGES`**；`get_agent` 向 `get_incremental_tools` 传入 `Settings`；示例包 **`sample_skill`**（`ping_sample_skill` 工具）与 `sample_skill.json` 配方。

### 历史新增（Asset Store 等）

- **Asset Store（案例库 / LanceDB）**：新增第四层“参考案例库”能力（整存整取、Dynamic Few-Shot，语感参考），设计见 `docs/ASSET_STORE.md`；`retrieve_ordered_context` 增加第④层，新增工具 `search_reference_cases`。
- **离线入库**：新增 CLI `agent-os-runtime asset-ingest <input>`（规则校验 + LLM gatekeeper + 特征抽取 + embedding + 写入）。
- **插件化开关**：新增 `AGENT_OS_ENABLE_ASSET_STORE` / `AGENT_OS_ASSET_STORE_PATH`、`AGENT_OS_ENABLE_HINDSIGHT`、`AGENT_OS_ENABLE_MEM0_LEARNING`。
- **依赖**：新增可选 extra `.[asset_store]`（包含 `lancedb`）。

## [0.6.0] - 2026-04-17

### 破坏性变更

- **主键改为 `skill_id`**：移除 **`AGENT_OS_PERSONA`**、**`--persona`** 与 **`Settings.agent_persona`**；CLI 使用 **`--skill`**；`get_agent(..., skill_id=...)`。
- **Manifest**：废弃 **`AGENT_OS_MANIFEST_PATH`**；改为 **`AGENT_OS_MANIFEST_DIR`** 目录扫描 + 包内置 **`data/skill_manifests/{skill_id}.json`** 注册表（`load_skill_manifest_registry`）；默认 skill：**`AGENT_OS_DEFAULT_SKILL_ID`**。
- **Graphiti / JSONL `group_id`**：由纯 `sanitize_group_id(client_id)` 改为 **`graphiti_group_id(client_id, skill_id)`**；**旧数据须按新键重新 ingest 或迁移**。

### 新增

- **`agent/skills/`**：**`get_incremental_tools(skill_id)`**（Phase 1 占位，返回空列表）；平台工具与增量工具在 `build_memory_tools` 内合并后再按 manifest 筛选。
- **`AgentManifestV1.agent_name`**：可选 Agno `Agent.name`（forge 模型已对齐可选字段）。
- **Web**：**`ChatIn.skill_id`**、**`AGENT_OS_WEB_SKILL_ID`**；记忆相关 API 增加可选 **`skill_id`** 以与对话 bundle 一致。

### 文档

- **`ENGINEERING.md` / `ARCHITECTURE.md` / `AGENTS.md` / `OPERATIONS.md`**：Skill 与复合 `group_id` 说明。

## [0.5.0] - 2026-04-17

> **0.6.0 起**：单文件 **`AGENT_OS_MANIFEST_PATH`** 及上述 **`doctor`** 检查已移除；以 **`AGENT_OS_MANIFEST_DIR`** 与注册表为准（见 **[0.6.0]**）。

### 新增

- **`AGENT_OS_MANIFEST_PATH`**（已于 0.6.0 废弃）：加载 **② forge** 导出的 `agent_config.json`（`AgentManifestV1`）：注入 `system_prompt`、按 `enabled_tools` 筛选工具、覆盖默认 `model`（可选）、展示 `handbook_version`。
- **`manifest_loader.py`**：与 forge 字段对齐的 Pydantic 模型（无运行时依赖 forge）。
- **`doctor`**（已于 0.6.0 调整）：曾检查 `AGENT_OS_MANIFEST_PATH` 文件是否存在。

### 修复

- **`retrieve_ordered_context`**：补全 `@tool` 装饰器（此前未注册为工具）。

## [0.4.0] - 2026-04-17

### 新增

- **MCP 探针（默认 fixture）**：包内 `resources/mcp_probe_default.json`；环境变量 **`AGENT_OS_MCP_PROBE_FIXTURE_PATH`** 可覆盖；Agent 工具 **`fetch_probe_context`**；可选 **`pip install -e ".[mcp]"`** 后运行 **`agent-os-runtime mcp-probe-server`** 或 **`python -m agent_os.mcp`**（stdio MCP，工具 `get_probe_snapshot`）。
- **端到端 Evaluator（规则门）**：`agent_os.evaluator.e2e`；CLI **`agent-os-runtime eval <case.json>`**（仅 Golden rules，无 LLM）；示例 `tests/core/fixtures/e2e_eval_case.json`。
- **离线领域知识（无 Neo4j）**：`agent_os.knowledge.jsonl_append`；CLI **`agent-os-runtime knowledge-append-jsonl -o path.jsonl --client-id X --text ...`**。
- **离线 Graphiti 写入（需 Neo4j + OpenAI）**：`knowledge/graphiti_ingest.py`；CLI **`agent-os-runtime graphiti-ingest episodes.json`**；**`--dry-run`** 仅校验 JSON；示例 `docs/examples/graphiti_episodes.example.json`。
- **`doctor`**：检查 **`AGENT_OS_MCP_PROBE_FIXTURE_PATH`**。

### 依赖

- 可选 **`[mcp]`**：`mcp` SDK（仅探针服务端与将来 MCP 客户端集成需要）。

### 文档（同步）

- `ARCHITECTURE.md`：探针、规则门、离线 ingest；修正「未实现」过时表述。
- `ENGINEERING.md`：目录树补充 `mcp/`、`graphiti_ingest`、`jsonl_append`、`resources/`。
- `README.md`、`.env.example`：新子命令与环境变量。
- 历史条目 `0.1.0` 中「后续阶段」说明已标注由后续版本实现。

## [0.3.2] - 2026-04-17

### 新增

- **`AGENT_OS_HANDOFF_MANIFEST_PATH` 注入 Agent**：`get_agent` 读取 `handbook_handoff.json` 摘要写入系统指令（条目数、校验通过/失败、时间、schema 引用）。
- **Golden rules（可选）**：环境变量 **`AGENT_OS_GOLDEN_RULES_PATH`** 指向 JSON 数组（`pattern` + `message` + 可选 `id`）；工具 **`check_delivery_text`**；示例 `data/golden_rules.example.json`。
- **记忆槽启发式工具**：**`suggest_memory_lane`**（不写入存储，辅助选择 `record_*`）。
- **`doctor`**：检查 `AGENT_OS_GOLDEN_RULES_PATH` 文件是否存在。

### 说明

- CI：仓库内 `.github/workflows/ci.yml` 跑 `pytest`（见工作流文件）。

## [0.3.1] - 2026-04-17

### 新增

- **`agent-os-runtime doctor`**：环境自检（OpenAI / Mem0 / Neo4j / graphiti / handoff / VIDEO_RAW_INGEST_ROOT）；`--strict`。
- **HTTP 重试**：`Mem0MemoryBackend` 与 Graphiti `search_` 路径使用 `agent_os.util.retry.retry_sync`（指数退避）。
- **配置**：`AGENT_OS_HANDOFF_MANIFEST_PATH`（元数据提示，可选）。

### 仓库外

- **`ops-stack/PIPELINE.md`**：①→②→③ 命令与变量说明。
- **`ops-knowledge`**：`ops-knowledge validate` / `manifest`（见该目录 README）。

## [0.3.0] - 2026-04-17

### 新增（阶段 c）

- **Hindsight**：`HindsightStore` 替代 stub；`feedback` / `lesson` 行；`MemoryController.search_hindsight`。
- **检索顺序**：工具 `retrieve_ordered_context`（Mem0 → Hindsight → Graphiti）；`search_past_lessons`。
- **AsyncReview**：`review/async_review.py`；CLI 退出时 `submit_and_wait` 写入教训；`AGENT_OS_ASYNC_REVIEW_*`；`--no-async-review`。
- **CLI**：`agent.run` 收集 transcript；`--task-id`；默认路径 `data/hindsight.jsonl`（`AGENT_OS_HISTORICAL_PATH`）。

### 破坏性变更

- 环境变量 `AGENT_OS_HISTORICAL_STUB_PATH` 仍兼容，优先使用 **`AGENT_OS_HISTORICAL_PATH`**；默认文件名改为 `data/hindsight.jsonl`。

## [0.2.0] - 2026-04-17

### 新增（阶段 b）

- **Graphiti 只读**：`GraphitiReadService` → `graphiti.search_`，租户 `group_id` 映射、`asyncio` 超时、BFS 深度可配。
- **JSONL 降级**：`AGENT_OS_KNOWLEDGE_FALLBACK_PATH`；示例 `docs/examples/knowledge_fallback.example.jsonl`。
- **Agent 工具**：`search_domain_knowledge`；CLI `--no-knowledge`。
- **可选依赖**：`pip install -e ".[graphiti]"`（`graphiti-core`）。

### 文档

- 更新 `ENGINEERING.md`、`ARCHITECTURE.md`、`OPERATIONS.md`。

## [0.1.0] - 2026-04-17

### 新增

- 阶段 **(a)** 垂直切片：**Agno Agent + Mem0 + 本地降级 + MemoryController + Hindsight JSONL 占位**。
- CLI：`python -m agent_os` 或安装后的 `agent-os-runtime`。
- 工程文档：`ENGINEERING.md`、`ARCHITECTURE.md`、`OPERATIONS.md`。

### 说明

- 初版范围说明；Graphiti 只读、AsyncReview、MCP/Evaluator 等已在 **0.2.0 起**各版本实现，见上方更新条目。

### 测试

- `tests/test_memory_controller.py`：`pytest`（`pyproject.toml` 中 `testpaths = ["tests"]`，避免收集 `.venv`）。
