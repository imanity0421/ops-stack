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
