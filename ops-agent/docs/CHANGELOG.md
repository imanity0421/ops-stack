# 变更日志

格式基于实际交付，版本号与 `pyproject.toml` / `ops_agent.__version__` 对齐。

## [Unreleased]

### 新增

- **Asset Store（案例库 / LanceDB）**：新增第四层“参考案例库”能力（整存整取、Dynamic Few-Shot，语感参考），设计见 `docs/ASSET_STORE.md`；`retrieve_ordered_context` 增加第④层，新增工具 `search_reference_cases`。
- **离线入库**：新增 CLI `ops-agent asset-ingest <input>`（规则校验 + LLM gatekeeper + 特征抽取 + embedding + 写入）。
- **插件化开关**：新增 `OPS_ENABLE_ASSET_STORE` / `OPS_ASSET_STORE_PATH`、`OPS_ENABLE_HINDSIGHT`、`OPS_ENABLE_MEM0_LEARNING`。
- **依赖**：新增可选 extra `.[asset_store]`（包含 `lancedb`）。

## [0.6.0] - 2026-04-17

### 破坏性变更

- **主键改为 `skill_id`**：移除 **`OPS_AGENT_PERSONA`**、**`--persona`** 与 **`Settings.agent_persona`**；CLI 使用 **`--skill`**；`get_agent(..., skill_id=...)`。
- **Manifest**：废弃 **`OPS_AGENT_MANIFEST_PATH`**；改为 **`OPS_AGENT_MANIFEST_DIR`** 目录扫描 + 包内置 **`data/skill_manifests/{skill_id}.json`** 注册表（`load_skill_manifest_registry`）；默认 skill：**`OPS_AGENT_DEFAULT_SKILL_ID`**。
- **Graphiti / JSONL `group_id`**：由纯 `sanitize_group_id(client_id)` 改为 **`graphiti_group_id(client_id, skill_id)`**；**旧数据须按新键重新 ingest 或迁移**。

### 新增

- **`agent/skills/`**：**`get_incremental_tools(skill_id)`**（Phase 1 占位，返回空列表）；平台工具与增量工具在 `build_memory_tools` 内合并后再按 manifest 筛选。
- **`AgentManifestV1.agent_name`**：可选 Agno `Agent.name`（forge 模型已对齐可选字段）。
- **Web**：**`ChatIn.skill_id`**、**`OPS_WEB_SKILL_ID`**；记忆相关 API 增加可选 **`skill_id`** 以与对话 bundle 一致。

### 文档

- **`ENGINEERING.md` / `ARCHITECTURE.md` / `AGENTS.md` / `OPERATIONS.md`**：Skill 与复合 `group_id` 说明。

## [0.5.0] - 2026-04-17

> **0.6.0 起**：单文件 **`OPS_AGENT_MANIFEST_PATH`** 及上述 **`doctor`** 检查已移除；以 **`OPS_AGENT_MANIFEST_DIR`** 与注册表为准（见 **[0.6.0]**）。

### 新增

- **`OPS_AGENT_MANIFEST_PATH`**（已于 0.6.0 废弃）：加载 **② forge** 导出的 `agent_config.json`（`AgentManifestV1`）：注入 `system_prompt`、按 `enabled_tools` 筛选工具、覆盖默认 `model`（可选）、展示 `handbook_version`。
- **`manifest_loader.py`**：与 forge 字段对齐的 Pydantic 模型（无运行时依赖 forge）。
- **`doctor`**（已于 0.6.0 调整）：曾检查 `OPS_AGENT_MANIFEST_PATH` 文件是否存在。

### 修复

- **`retrieve_ordered_context`**：补全 `@tool` 装饰器（此前未注册为工具）。

## [0.4.0] - 2026-04-17

### 新增

- **MCP 探针（默认 fixture）**：包内 `resources/mcp_probe_default.json`；环境变量 **`OPS_MCP_PROBE_FIXTURE_PATH`** 可覆盖；Agent 工具 **`fetch_ops_probe_context`**；可选 **`pip install -e ".[mcp]"`** 后运行 **`ops-agent mcp-probe-server`** 或 **`python -m ops_agent.mcp`**（stdio MCP，工具 `get_ops_probe_snapshot`）。
- **端到端 Evaluator（规则门）**：`ops_agent.evaluator.e2e`；CLI **`ops-agent eval <case.json>`**（仅 Golden rules，无 LLM）；示例 `tests/fixtures/e2e_eval_case.json`。
- **离线领域知识（无 Neo4j）**：`ops_agent.knowledge.jsonl_append`；CLI **`ops-agent knowledge-append-jsonl -o path.jsonl --client-id X --text ...`**。
- **离线 Graphiti 写入（需 Neo4j + OpenAI）**：`knowledge/graphiti_ingest.py`；CLI **`ops-agent graphiti-ingest episodes.json`**；**`--dry-run`** 仅校验 JSON；示例 `docs/examples/graphiti_episodes.example.json`。
- **`doctor`**：检查 **`OPS_MCP_PROBE_FIXTURE_PATH`**。

### 依赖

- 可选 **`[mcp]`**：`mcp` SDK（仅探针服务端与将来 MCP 客户端集成需要）。

### 文档（同步）

- `ARCHITECTURE.md`：探针、规则门、离线 ingest；修正「未实现」过时表述。
- `ENGINEERING.md`：目录树补充 `mcp/`、`graphiti_ingest`、`jsonl_append`、`resources/`。
- `README.md`、`.env.example`：新子命令与环境变量。
- 历史条目 `0.1.0` 中「后续阶段」说明已标注由后续版本实现。

## [0.3.2] - 2026-04-17

### 新增

- **`OPS_HANDOFF_MANIFEST_PATH` 注入 Agent**：`get_agent` 读取 `handbook_handoff.json` 摘要写入系统指令（课数、校验通过/失败、时间、schema 引用）。
- **Golden rules（可选）**：环境变量 **`OPS_GOLDEN_RULES_PATH`** 指向 JSON 数组（`pattern` + `message` + 可选 `id`）；工具 **`check_delivery_text`**；示例 `data/golden_rules.example.json`。
- **记忆槽启发式工具**：**`suggest_memory_lane`**（不写入存储，辅助选择 `record_*`）。
- **`doctor`**：检查 `OPS_GOLDEN_RULES_PATH` 文件是否存在。

### 说明

- CI：仓库内 `.github/workflows/ci.yml` 跑 `pytest`（见工作流文件）。

## [0.3.1] - 2026-04-17

### 新增

- **`ops-agent doctor`**：环境自检（OpenAI / Mem0 / Neo4j / graphiti / handoff / VIDEO_RAW_INGEST_ROOT）；`--strict`。
- **HTTP 重试**：`Mem0MemoryBackend` 与 Graphiti `search_` 路径使用 `ops_agent.util.retry.retry_sync`（指数退避）。
- **配置**：`OPS_HANDOFF_MANIFEST_PATH`（元数据提示，可选）。

### 仓库外

- **`ops-stack/PIPELINE.md`**：①→②→③ 命令与变量说明。
- **`ops-knowledge`**：`ops-knowledge validate` / `manifest`（见该目录 README）。

## [0.3.0] - 2026-04-17

### 新增（阶段 c）

- **Hindsight**：`HindsightStore` 替代 stub；`feedback` / `lesson` 行；`MemoryController.search_hindsight`。
- **检索顺序**：工具 `retrieve_ordered_context`（Mem0 → Hindsight → Graphiti）；`search_past_lessons`。
- **AsyncReview**：`review/async_review.py`；CLI 退出时 `submit_and_wait` 写入教训；`OPS_ASYNC_REVIEW_*`；`--no-async-review`。
- **CLI**：`agent.run` 收集 transcript；`--task-id`；默认路径 `data/hindsight.jsonl`（`OPS_HISTORICAL_PATH`）。

### 破坏性变更

- 环境变量 `OPS_HISTORICAL_STUB_PATH` 仍兼容，优先使用 **`OPS_HISTORICAL_PATH`**；默认文件名改为 `data/hindsight.jsonl`。

## [0.2.0] - 2026-04-17

### 新增（阶段 b）

- **Graphiti 只读**：`GraphitiReadService` → `graphiti.search_`，租户 `group_id` 映射、`asyncio` 超时、BFS 深度可配。
- **JSONL 降级**：`OPS_KNOWLEDGE_FALLBACK_PATH`；示例 `docs/examples/knowledge_fallback.example.jsonl`。
- **Agent 工具**：`search_domain_knowledge`；CLI `--no-knowledge`。
- **可选依赖**：`pip install -e ".[graphiti]"`（`graphiti-core`）。

### 文档

- 更新 `ENGINEERING.md`、`ARCHITECTURE.md`、`OPERATIONS.md`。

## [0.1.0] - 2026-04-17

### 新增

- 阶段 **(a)** 垂直切片：**Agno Agent + Mem0 + 本地降级 + MemoryController + Hindsight JSONL 占位**。
- CLI：`python -m ops-agent`。
- 工程文档：`ENGINEERING.md`、`ARCHITECTURE.md`、`OPERATIONS.md`。

### 说明

- 初版范围说明；Graphiti 只读、AsyncReview、MCP/Evaluator 等已在 **0.2.0 起**各版本实现，见上方更新条目。

### 测试

- `tests/test_memory_controller.py`：`pytest`（`pyproject.toml` 中 `testpaths = ["tests"]`，避免收集 `.venv`）。
