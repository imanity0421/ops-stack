# 开发会话日志

> **约定**：每次重要开发段落或换机器前，在下方追加一节；另一台电脑先读最近 1～2 节再动手。  
> 机器名可通过环境变量 `CODING_SYNC_MACHINE` 区分（如 `office` / `home`）。  
> **本目录已迁入 `ops-stack/coding-sync/`，与仓库同步。**

---

## 模板（复制后填写）

**日期**：YYYY-MM-DD  
**机器**：office / home（或 `CODING_SYNC_MACHINE`）  
**主要仓库/分支**：例如 `ops-stack` / `main`  

**本轮做了什么**（列表）：

- 

**运行过的关键命令**（可选，详细见 `runs.jsonl`）：

- 

**未竟 / 下一台机器先做**：

- 

**备注**（环境差异、密钥、路径）：

- 

---

## 2026-04-17T11:54:45+08:00 | LAPTOP-VPIF7FP8

**标题**：机制初始化

新增 coding-sync 目录、log_session 脚本与 Cursor 规则 alwaysApply。

---

## 2026-04-17 | 迁移

**标题**：迁入 ops-stack 仓库

`coding-sync` 自工作区根目录迁入 `ops-stack/coding-sync/`，便于与唯一同步仓库 `ops-stack` 一并 pull/push。

---

## 2026-04-17 | skill_id 与 Graphiti 复合分区

**标题**：Phase 1 单 Agent 多 Skill 骨架落地

**本轮做了什么**：

- **主键 `skill_id`**：移除 `persona` / `OPS_AGENT_PERSONA`；`get_agent(..., skill_id=...)`；CLI `--skill`；Web `ChatIn.skill_id` + `OPS_WEB_SKILL_ID`。
- **Manifest 注册表**：`OPS_AGENT_MANIFEST_DIR` 扫描 + 包内 `src/ops_agent/data/skill_manifests/*.json`；`load_skill_manifest_registry` / `resolve_effective_skill_id`。
- **Graphiti / JSONL**：`graphiti_group_id(client_id, skill_id)`；`graphiti_ingest` 支持顶层 `default_skill_id` 与逐条 `skill_id`。
- **工具**：`build_memory_tools` 合并 `get_incremental_tools(skill_id)`（当前占位空列表）。
- **文档**：ENGINEERING / ARCHITECTURE / AGENTS / OPERATIONS / CHANGELOG、根 PIPELINE / NAMING / PROJECT_CONTEXT、forge `AgentManifestV1.agent_name`、pipeline-demo `env_snippet`。

**运行过的关键命令**：

- `cd ops-agent && python -m pytest`（32 passed）
- `cd ops-distiller-forge && python -m pytest`（5 passed）

**未竟 / 下一台机器先做**：

- 生产 Neo4j 中旧 `group_id` 仅租户维度的数据需 **重新 ingest 或迁移** 后检索才命中。
- Web 前端若切换 skill，记忆/复盘 API 需同步传 `skill_id`（已支持 query/body，前端可接 localStorage）。

**备注**：

- 破坏性环境变量：`OPS_AGENT_MANIFEST_PATH` → `OPS_AGENT_MANIFEST_DIR`；配方文件需命名为 `{skill_id}.json`（如 `default_ops.json`）。

---

## 2026-04-17 | 换机前：文档固化与推送

**标题**：GitHub `origin/main` 同步 + 内置 manifest 入库

**本轮做了什么**：

- **`PROJECT_CONTEXT.md`**：增加「最近合入」换机接续摘要（skill_id / manifest 目录 / 复合 `group_id`）。
- **`ops-agent/.gitignore`**：将泛匹配 `data/` 改为仅 **`/data/`**，使 **`src/ops_agent/data/skill_manifests/*.json`** 纳入版本控制（此前被误忽略，另一台机器 pull 会缺内置配方）。
- **`coding-sync/SESSION_LOG.md`**：本节前已记录 Phase 1 实现；本节标记 **已 push** 供下一台机器 `git pull`。

**运行过的关键命令**：

- `pytest -q`（ops-agent 32 passed）；`ruff check src tests`（通过）。

**下一台机器**：

- 在 **`ops-stack`** 根执行 **`git pull`**；按 **`PROJECT_CONTEXT.md`** 与 **`ops-agent/docs/OPERATIONS.md`** 配置 `.env`（注意 **`OPS_AGENT_MANIFEST_DIR`** 等变量名变更）。

---

## 2026-04-24 | Asset Store（案例库）落地（LanceDB）

**标题**：新增“参考案例库”第四层记忆 + 离线入库管线骨架

**本轮做了什么**：

- **设计稿落盘**：新增 `ops-agent/docs/ASSET_STORE.md`，定义整存整取（case-level）+ 向量仅对特征文本（摘要+风格指纹+标签）+ 插件化开关 + 离线 ingestion pipeline。
- **架构文档更新**：`docs/ARCHITECTURE.md` / `docs/ENGINEERING.md` / `docs/OPERATIONS.md` 增加 Asset Store 第④层说明与配置变量。
- **实现 Asset Store 封装**：新增 `src/ops_agent/knowledge/asset_store.py`（LanceDB 封装 + `NullAssetStore`），并加可选依赖 extra：`pip install -e ".[asset_store]"`。
- **运行时工具接入**：`retrieve_ordered_context` 扩展为 ①Mem0→②Hindsight→③Graphiti→④Asset Store；新增工具 `search_reference_cases`。**factory 不直接查库**，仅传入 store/开关并挂工具。
- **插件化开关**：新增环境变量
  - `OPS_ENABLE_ASSET_STORE` / `OPS_ASSET_STORE_PATH`
  - `OPS_ENABLE_HINDSIGHT`（可完全关闭 hindsight 存储与工具挂载）
  - `OPS_ENABLE_MEM0_LEARNING`（关闭 Mem0 写入工具但仍可读）
- **离线入库 CLI**：新增 `ops-agent asset-ingest <input>`（规则校验 + LLM gatekeeper + LLM 特征抽取 + embedding + 写入）。
- **测试**：新增/更新单测，`pytest` 全通过。

**运行过的关键命令**：

- `cd ops-agent; python -m pytest`（36 passed）

**未竟 / 下一台机器先做**：

- 若要更强的数据治理：在 `asset_ingest.py` 增加去重/合规规则/人工复核流（status=quarantined 的处理）。
- 若运行时 embedding 成本/延迟过高：考虑本地 embedding 或缓存 query embedding（保持“秒查秒回”体验）。

**2026-04-24 补充（风险修复 + 生产向能力）**：

- `get_agent` 在 `OPS_ENABLE_ASSET_STORE=1` 且未显式传入 `asset_store` 时自动 `asset_store_from_settings`，避免「只开开关不生效」。
- LanceDB 检索改为**向量多取 + 内存按租户过滤**，去掉无过滤回退，避免多租户数据串案。
- 入库：**强指纹** `dedup_key`、可选 **L2 近似去重**（`OPS_ASSET_NEAR_DEDUP_L2_MAX`）、**每 skill 硬合规**（`OPS_SKILL_COMPLIANCE_DIR`）；运行时新增工具 `check_skill_compliance_text`。
- 回退：`ops-agent asset-rm` 支持按 `case_id` 或 `client_id+skill+--all-skill` 清库块。
- 未传 `user_id` 的检索只命中**租户共享**（`user_id` 为空的行），避免多用户互串。

---

## 2026-04-24 | Agent OS 定版路线图文档

**标题**：Sprint 1–4 实施表、DoD、Mermaid 设计图落地为 `docs/AGENT_OS_ROADMAP.md`

- 定版内容：Skill 白名单与工厂边界、会话持久化与多机预留、宪法与交付契约、可观测与显式 target 的 ingest、per-skill eval、备份 SOP；`ENGINEERING.md` §7.1 引用；`CHANGELOG.md` [Unreleased] 文档条目。

---

## 2026-04-24 | Sprint1 P0-1：Skill 包白名单动态加载

- **`OPS_AGENT_LOADABLE_SKILL_PACKAGES`** + `ops_agent.agent.skills.loader`；`get_incremental_tools(skill_id, settings=...)`；示例包 **`toy_skill`** / `ping_toy_skill`；`pytest` 全绿。

---

## 2026-04-24 | Sprint1 P0-2：Agno 会话持久化

- **`ops_agent.agent.session_db.create_session_db`**：`OPS_ENABLE_SESSION_DB` / `OPS_SESSION_DB_PATH` / `OPS_SESSION_DB_URL` / `OPS_SESSION_HISTORY_MAX_MESSAGES`；`get_agent` 挂 `db` + `add_history_to_context`（N>0 时）。
- **Web**：`GET /api/session/messages`（进程重启后按 `session_id` 拉消息）；`/api/agent/inspect` 的 `paths.session_persistence` 元数据；`examples/web_chat_fastapi.py` 文档说明。
- **文档**：`OPERATIONS.md`、`.env.example`、`CHANGELOG`、`AGENT_OS_ROADMAP` §11；`tests/test_session_persistence.py`。

---

## 2026-04-24 | Sprint2 P1：宪法 + 交付物 `structured_v1`

- **P1-3**：`agent/constitutional.py` 段首注入；`OPS_ENABLE_CONSTITUTIONAL`；`AgentManifestV1.constitutional_prompt`；`docs/examples/constitutional_test_cases.md`；`tests/test_p1_constitutional_output.py`。
- **P1-4**：`manifest_output.OpsPlanStructuredV1` + `resolve_structured_output_model`；`get_agent` 在 `structured_v1` 时设 `output_schema`+`structured_outputs`；包内 skill **`planning_draft`**。
- **ENGINEERING.md** §3.7、目录树更新。

---

## 2026-04-24 | Sprint3 P2：可观测 + POST /ingest

- **P2-5**：`observability.py`（`OPS_OBS`）、Web `X-Request-ID` 中间件、`/chat` 后结构化日志。
- **P2-6**：`ingest_gateway.py`、`POST /ingest`（显式 target）、`docs/examples/ingest_post_samples.md`；`OPS_INGEST_ALLOW_LLM`；单测 `test_observability.py` / `test_ingest_gateway.py`（asset 需 `lancedb` 时跑）。

---

## 2026-04-24 | Sprint4 P3：按 skill 评测 + 本地备份

- **P3-7**：`tests/skills/short_video`、`tests/skills/business` + markers；`pyproject` 登记 markers；`tests/skills/README.md`。
- **P3-8**：`backup_data_core.py`、`scripts/backup_data.py`、`docs/DATA_BACKUP.md`；`backups/` 与 `.gitignore`；`test_backup_data_core.py`。

---

## 2026-04-24 | 收尾：E402、token 聚合、Web 结构化、dev+lancedb

- 模块 docstring 顺序；`observability.details` 聚合；`ChatOut` 增加 `reply_content_kind`/`structured`；`pyproject` 的 `dev` extra 增加 `lancedb`；`OPERATIONS` 安装说明。



---

## 2026-04-25T23:24:24+08:00 | LAPTOP-VPIF7FP8

**标题**：ops-agent P1.5 cognitive stability

Implemented first batch of ops-agent P1.5 cognitive stability work: documented Ephemeral Metadata / Memory Policy / Temporal Grounding / Session Summarizer plan; kept Procedural Memory via Forge as pending_review-only TODO; added runtime_context prompt injection for cli/web/api; added Memory Policy gate and tool descriptions; added recorded_at/source metadata and temporal rendering for Mem0/Hindsight/Asset hits; updated env/docs/tests. Verified: python -m ruff check src tests; python -m ruff format --check src tests examples; python -m pytest -q --tb=short.


---

## 2026-04-26T00:23:40+08:00 | LAPTOP-VPIF7FP8

**标题**：ops-agent task-aware working memory

Recorded Task-aware Working Memory design: session-local task_id, conservative boundary detection, candidate/confirmed states, limited retrospective reassignment, audit, debug visibility, and no cross-session task memory. Implemented first batch: ops_agent.agent.task_memory SQLite schema/store, task_id generation, message append, summary upsert/get, task index, prompt helpers; Settings task-memory flags; get_agent injection for current_task_summary and session_task_index; tests for store and prompt injection. Verified full: python -m ruff format src tests examples; python -m ruff check src tests; python -m ruff format --check src tests examples; python -m pytest -q --tb=short.


---

## 2026-04-26T00:26:50+08:00 | LAPTOP-VPIF7FP8

**标题**：ops-agent generic wording cleanup

Removed scenario-specific wording from generic task-memory/memory-policy tests and one generic agent hint: replaced short-video /朋友圈/预算 style examples with neutral task/deliverable wording. Left intentional existing skill/domain assets (short_video manifest, skill-specific tests, ops-agent product docs/examples) unchanged. Verified: python -m ruff check src tests; python -m ruff format --check src tests examples; python -m pytest -q --tb=short.


---

## 2026-04-26T00:42:00+08:00 | LAPTOP-VPIF7FP8

**标题**：agent-os-runtime non-conservative cleanup

Renamed runtime directory and package from ops-agent / ops_agent to agent-os-runtime / agent_os; renamed CLI entrypoint and env prefixes to AGENT_OS_*; removed built-in vertical/test manifests and old skill-specific test directories; kept only neutral default_agent and planning_draft manifests. Reworked skill-loader tests to use external manifest + in-memory sample skill package, moved core tests under tests/core, renamed probe tools to fetch_probe_context / get_probe_snapshot, and removed old project/domain wording from core code/tests/docs. Verified: python -m ruff check src tests; python -m ruff format --check src tests; python -m pytest.

