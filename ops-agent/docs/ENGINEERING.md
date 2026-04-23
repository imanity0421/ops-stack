# ops-agent 工程方案依据（长期维护）

本文档为 **ops-agent** 仓库的权威技术说明，与实现代码同步演进。总体产品愿景与多仓库分工见上级产品方案；本仓库覆盖 **阶段 (a)+(b)+(c)：Agno + Mem0 + MemoryController + Graphiti 只读 + Hindsight + AsyncReview** 的实现边界与契约。

---

## 1. 目标与边界

### 1.1 本仓库负责

- 基于 **Agno** 编排可运行的 **专项运营顾问 Agent**（非通用贾维斯）。
- **MemoryController** 作为 **唯一推荐写入入口**，向 **Mem0**（或本地降级后端）写入 **客户事实与稳定偏好**；向 **Hindsight**（`HindsightStore`，JSONL）写入 **任务级反馈**；**AsyncReview** 在会话结束时提炼 **教训** 并追加写入同一存储。
- 提供 CLI 与可导入的 **工厂函数**（`get_agent` / `get_reasoning_agent`），便于调试与后续接入 FastAPI/AgentOS。

### 1.2 本仓库不负责（明确排除）

- **① `video-raw-ingest`**（目录名可在 **`ops-stack.toml`** 配置；PyPI/CLI 名仍为 `video-raw-ingest`）：原始视频转写与合并。
- **DSPy 炼丹项目**：运营知识手册生成与 prompt 制品（独立仓库）；本仓库通过环境变量与后续「手册版本」接入，不在 (a) 强制实现。
- **Graphiti / Neo4j**：阶段 **(b) 已接入只读**（`search_`）；**禁止**在运行时调用 `add_episode` 等写入 API；图谱数据由离线/独立管线写入。
- **MCP 旁路探针**：默认 **fixture 工具** `fetch_ops_probe_context`；可选 **stdio MCP**（`ops-agent mcp-probe-server`，依赖 `[mcp]`）。
- **完整商业化**：鉴权、计费、多租户控制台 UI 不在 (a) 范围。

---

## 2. 总体上下文中的位置

```
video-raw-ingest (①)  →  原始结构化产物
        ↓
ops-knowledge / ops-distiller-forge（②）→  校验、handoff、知识点与 Manifest
        ↓
ops-agent（本仓库，③）→  运行时 Agent + Mem0 + Graphiti 只读
```

**跨仓契约（建议尽早冻结）**

| 字段 | 说明 |
|------|------|
| `client_id` | 租户/客户隔离键；所有记忆写入与检索必须携带 |
| `user_id` | 可选；终端用户；与 `client_id` 组合成 Mem0 `user_id` |
| 手册版本 | 后续：`OPS_HANDBOOK_VERSION` 或制品路径，与 prompt 对齐 |

---

## 3. 架构要点

### 3.1 认知分工（与总方案一致）

| 模块 | 行为 |
|------|------|
| **Mem0 / 本地后端** | 存储 **属性（attribute）** 与 **稳定偏好（preference）**；唯一长期记忆主存储 |
| **Hindsight** | **`HindsightStore`**：`data/hindsight.jsonl`（`OPS_HISTORICAL_PATH`）；含 `feedback` 与 `lesson`；检索 `search_hindsight` / 工具 `search_past_lessons` |
| **Graphiti** | **只读**：`GraphitiReadService.search_domain_knowledge` → `graphiti.search_`；分区键 **`graphiti_group_id(client_id, skill_id)`**（`client` 与 `skill` 两段经 `sanitize_group_id` 后以 `__` 拼接），须与 **`graphiti-ingest`**、JSONL fallback 写入一致；BFS 深度默认 **2**（`OPS_GRAPHITI_BFS_MAX_DEPTH`） |
| **Skill / Manifest** | 主键 **`skill_id`**（如 `default_ops`、`short_video`）。`manifest_loader.load_skill_manifest_registry`：先读包内 `data/skill_manifests/*.json`，再合并 **`OPS_AGENT_MANIFEST_DIR`** 下同名文件覆盖。`get_agent(..., skill_id=...)` 未传时用 **`OPS_AGENT_DEFAULT_SKILL_ID`**。已弃用 **`OPS_AGENT_PERSONA`** / **`OPS_AGENT_MANIFEST_PATH`**。 |
| **工具合并** | **平台工具**（`build_memory_tools`）常驻 + **`get_incremental_tools(skill_id)`** 增量（当前为空占位）；再按 manifest `enabled_tools` 筛选。 |

### 3.2 检索顺序（阶段 c）

- **工具 `retrieve_ordered_context`**：固定顺序 **① Mem0（`search_profile`）→ ② Hindsight（`search_hindsight`）→ ③ Graphiti（若已挂载）**。
- 仍保留单独工具 `search_client_memory`、`search_past_lessons`、`search_domain_knowledge` 供细粒度调用。

### 3.3 领域知识降级

- 若 Neo4j 未配置、检索超时或抛错，则使用 **`OPS_KNOWLEDGE_FALLBACK_PATH`** 指向的 JSONL（`KnowledgeJsonlFallback`），按 `group_id` 过滤后做简单词重叠排序。
- 若两者皆不可用，工具返回说明字符串，提示配置环境变量。

### 3.4 写入路由（记忆层）

- `MemoryLane.ATTRIBUTE` → Mem0（或本地 JSON）。
- `MemoryLane.TASK_FEEDBACK` → `HindsightStore`（`type=feedback`）。
- **幂等**：`MemoryController` 对 `(client_id, lane, text)` 做指纹去重，防止同句重复刷写。

### 3.5 双轨同步（与总方案对齐）

- **Mem0 轨**：工具调用即时写入；CLI 中每轮对话后 `bump_turn_and_maybe_snapshot`，每 N 轮（默认 5）触发快照钩子（Mem0 托管侧可为 no-op，本地侧打日志）。
- **Hindsight 轨**：工具写入反馈；**AsyncReview**（`review/async_review.py`）在 CLI 退出时调用 LLM 生成 `lesson` 行并写入；`submit_and_wait` 避免进程过早退出。

### 3.6 Agno 集成原则

- **逻辑重于命名**：对外只依赖 `ops_agent.agent.factory` 中工厂函数；Agno 类名变更时优先改工厂内实现。
- **推理路由**：`get_agent(..., thought_mode="fast"|"slow")`；`slow` 时启用 Agno `reasoning`。**若模型不支持导致异常**，应优先使用 `fast` 或升级模型（见 OPERATIONS 排障）。

---

## 4. 代码目录约定

```
ops-agent/
  pyproject.toml
  README.md
  docs/
    ENGINEERING.md    ← 本文件
    ARCHITECTURE.md
    OPERATIONS.md
    CHANGELOG.md
  src/ops_agent/
    __init__.py
    __main__.py
    config.py              # 环境配置
    cli.py                 # 命令行入口
    handoff.py             # handbook_handoff.json → 指令摘要
    evaluator/             # Golden rules、e2e 规则门
    mcp/                   # 探针 fixture + 可选 stdio 服务
    memory/
      models.py            # Pydantic：UserFact、MemoryLane 等
      controller.py        # MemoryController
      backends/            # Mem0 / 本地
      hindsight_store.py
    review/
      async_review.py        # AsyncReview 服务
    knowledge/
      graphiti_reader.py   # Graphiti 只读 + JSONL fallback
      graphiti_ingest.py   # 离线 add_episode（CLI，需 Neo4j+LLM）
      jsonl_append.py      # JSONL 降级追加
      fallback.py
      group_id.py
    resources/             # 包内默认 JSON（如 mcp_probe_default.json）
    data/skill_manifests/  # 内置 skill 配方 JSON（default_ops / short_video）
    agent/
      factory.py           # get_agent / get_reasoning_agent
      tools.py             # Agno 工具（绑定 client_id + skill_id → Graphiti 分区）
      skills/              # skill 增量工具（get_incremental_tools）
```

---

## 5. 环境与配置

见 [OPERATIONS.md](OPERATIONS.md)。关键变量：

- `OPENAI_API_KEY`：必需（对话与工具）。
- `OPENAI_API_BASE`：可选，兼容中转。
- `MEM0_API_KEY`：可选；未设置则使用本地 `data/local_memory.json`。
- `OPS_AGENT_MODEL`：默认 `gpt-4o-mini`。
- `OPS_SNAPSHOT_EVERY_N_TURNS`：默认 `5`。
- **Graphiti（可选）**：`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`；`OPS_GRAPHITI_SEARCH_TIMEOUT_SEC`、`OPS_GRAPHITI_MAX_RESULTS`、`OPS_GRAPHITI_BFS_MAX_DEPTH`。
- **降级 JSONL**：`OPS_KNOWLEDGE_FALLBACK_PATH`（示例见 `docs/examples/knowledge_fallback.example.jsonl`）。
- **Hindsight 路径**：`OPS_HISTORICAL_PATH`（兼容 `OPS_HISTORICAL_STUB_PATH`），默认 `data/hindsight.jsonl`。
- **AsyncReview**：`OPS_ASYNC_REVIEW_ON_EXIT`（默认 `1`）、`OPS_ASYNC_REVIEW_MODEL`（可选）。

安装 Graphiti 依赖：`pip install -e ".[graphiti]"`。

- **管线自检**：`ops-agent doctor`；可选 **`OPS_HANDOFF_MANIFEST_PATH`**（`ops-knowledge manifest` 产出；**运行时注入** `get_agent` 指令摘要）、**`VIDEO_RAW_INGEST_ROOT`**（校验 ① schema 路径）。工作区总览见上级 `PIPELINE.md`。
- **交付规则抽检（可选）**：**`OPS_GOLDEN_RULES_PATH`** → JSON 数组正则规则；Agent 工具 **`check_delivery_text`**；示例见 `data/golden_rules.example.json`。
- **Skill 配方目录（可选）**：**`OPS_AGENT_MANIFEST_DIR`** → 扫描其中 **`*.json`**，文件名为 **`skill_id`**；可将 **`export-manifest`** 输出复制为 **`default_ops.json`** 等。未设置时仍使用包内置配方。默认 skill：**`OPS_AGENT_DEFAULT_SKILL_ID`**（默认 `default_ops`）。见 **`manifest_loader.py`**。
- **探针 fixture（可选覆盖）**：**`OPS_MCP_PROBE_FIXTURE_PATH`**；CLI **`ops-agent eval`**、**`knowledge-append-jsonl`**、**`graphiti-ingest`** 见 [OPERATIONS.md](OPERATIONS.md)。

---

## 6. 安全与租户

- 所有记忆操作必须通过 **工具** 或 **MemoryController**，并传入 **`client_id`**。
- Mem0 侧使用 `user_id = f"{client_id}::{user_id}"`（`Mem0MemoryBackend.mem_user_id`），避免跨租户串数据。
- 日志不得打印完整客户话术到公共遥测；生产环境需脱敏策略（后续里程碑）。

---

## 7. 阶段路线图（本仓库内）

| 阶段 | 内容 |
|------|------|
| **(a)** | Agno + Mem0 + MemoryController + Hindsight 存储 + CLI |
| **(b)** | Graphiti **只读**（`search_`）+ JSONL 降级 + `group_id` 租户映射 + CLI `--no-knowledge` |
| **(c) 当前** | **`retrieve_ordered_context`**、**AsyncReview**、**handoff**、**Golden rules**、**`suggest_memory_lane`**、**MCP fixture + 可选 MCP 服务**、**`ops-agent eval` 规则门**、**JSONL 离线知识追加**、**Graphiti 离线 ingest（需 Neo4j+LLM）** |

每阶段更新 **CHANGELOG** 与本文件 **§7**。

---

## 8. 设计决策记录（轻量 ADR）

| ID | 决策 | 原因 |
|----|------|------|
| ADR-001 | 无 Mem0 时落盘本地 JSON | 降低本地与 CI 门槛，避免强依赖云端 |
| ADR-002 | Hindsight 先用 JSONL | 先跑通「任务反馈」数据流，阶段 (c) 再换服务 |
| ADR-003 | 工厂函数集中创建 Agent | 隔离 Agno API 变更面 |
| ADR-004 | Graphiti 仅 `search_`，不写 episode | 运行时只读，与离线建图解耦 |
| ADR-005 | `graphiti-core` 为可选 extra | 无 Neo4j 环境仍可安装核心 Agent |
| ADR-006 | AsyncReview 用独立线程 + `join` | 避免 daemon 线程被进程退出打断 |

---

## 9. 变更流程

1. 行为或契约变化 → 更新 **ENGINEERING.md** 相应章节。
2. 用户可见行为 → **CHANGELOG.md** 追加条目并升版本号（`pyproject.toml` / `ops_agent.__version__`）。
3. 运维步骤变化 → **OPERATIONS.md**。
4. **独立拷贝 / 跨机器 Cursor 研发**（合回 `ops-stack` 前）→ 遵守仓库根 [**AGENTS.md**](../AGENTS.md) 中的依赖边界与自检清单。

---

## 10. 参考

- Agno 文档：<https://docs.agno.com>
- Mem0 文档：<https://docs.mem0.ai>
- Graphiti：<https://help.getzep.com/graphiti/graphiti/overview>
- 上游数据：**`video-raw-ingest`**（总工程见 `ops-stack/PIPELINE.md` 与 `ops-stack.toml`）
