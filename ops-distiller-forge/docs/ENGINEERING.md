# ops-distiller-forge 工程方案依据（长期维护）

本文档为 **②b 阶段炼金工坊** 的权威技术说明，与代码同步演进。总体管线见 **`ops-stack/PIPELINE.md`**；与 **① `video-raw-ingest`**、**③ `ops-agent`** 的契约见 `docs/CONTRACT.md`（子目录名以 **`ops-stack.toml`** 为准）。

---

## 1. 目标与边界

### 1.1 本仓库负责

- **方法论母版（B）**：以 **Pydantic** 定义 `KnowledgePoint` 等 Ontology，作为唯一结构化真源（与「随缘图谱抽取」解耦）。
- **Map 阶段**：从 `lesson_merged.json` 生成知识点（默认 **确定性、无 API**，便于 CI；可选 **`--use-dspy`** + `dspy-ai` + `OPENAI_API_KEY`）。
- **Reduce 阶段（占位）**：跨课归并与聚簇接口预留；当前为粗策略，后续接 embedding/聚类与人工 gate。
- **Episode 投影**：将 `KnowledgePoint` 拼成 **短文**，输出与 **`ops-agent graphiti-ingest`** 兼容的 JSON 批次。
- **评测基线**：`eval-recall` 关键词 recall（后续替换为 Embedding + LLM Judge）。
- **Manifest**：导出 **`AgentManifestV1`**（`agent_config` 风格），供 ③ **Loader + tool_registry** 对接（加载逻辑在 `ops-agent` 侧实现）。

### 1.2 本仓库不负责

- **① 视频转写与 merged 生成**（`ops-stack` 下默认目录 **`video-raw-ingest`**，可配置）。
- **③ 运行时 Agent、Mem0、Hindsight、Graphiti 只读服务**（`ops-agent`）。
- **轻量校验与 handoff 清单**：仍由 **②a `ops-knowledge`**（`validate` / `manifest` / `dspy-stub`）承担；本仓不重复实现 schema 校验 CLI。

### 1.3 设计原则（与方案讨论一致）

| 原则 | 说明 |
|------|------|
| **B 投影到 Episode** | 真源为 JSONL/SQLite 中的结构化知识点；入库 Graphiti 的是 **投影短文**，非原始 JSON。 |
| **版本与溯源** | `LineageMeta`：`handbook_version`、`source_relpath`、`source_sha256`、`ingested_at_utc`。 |
| **与 ③ 解耦** | 独立依赖；可选 `dspy` extra；GPU 非必需（多数为 HTTP LLM）。 |

---

## 2. 架构与数据流

```
lesson_merged.json (①)
        ↓
   [map] KnowledgePoint*  →  JSONL（Git 真源） + 可选 SQLite 索引
        ↓
   [reduce]（占位）→ 去重/聚簇后的 KnowledgePoint*
        ↓
   [episodes] EpisodeBatchFile  →  ops-agent graphiti-ingest / add_episode 批处理
        ↓
   [export-manifest] AgentManifestV1  →  agent_config.json（③ Loader）
```

---

## 3. 目录约定

```
ops-distiller-forge/
  pyproject.toml
  README.md
  docs/
    ENGINEERING.md    ← 本文件
    CONTRACT.md
    CHANGELOG.md
  src/ops_distiller_forge/
    config.py
    ontology/models.py
    storage/jsonl_store.py, sqlite_store.py
    pipeline/map_stage.py, reduce_stage.py, episode_projector.py
    distill/dspy_map.py      # 可选，需 [dspy]
    metrics/coverage.py
    export/manifest.py
    cli.py
```

---

## 4. 核心类型

- **`KnowledgePoint`**：`title`、`theory_logic`、`sop_steps`、`key_metrics`、`anti_patterns`、`case_reference`、`metadata`、`cluster_key`。
- **`EpisodeRecord` / `EpisodeBatchFile`**：与 `ops-agent` 示例 `docs/examples/graphiti_episodes.example.json` 对齐。
- **`AgentManifestV1`**：`manifest_version`、`handbook_version`、`system_prompt`、`model`、`temperature`、`enabled_tools`（字符串 id，与 ③ 工具名对齐）。

---

## 5. 环境变量

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | `--use-dspy` 时必需 |
| `OPENAI_API_BASE` | 可选，兼容中转 |
| `OPS_FORGE_DSPY_MODEL` | 默认 `openai/gpt-4o-mini` |
| `OPS_HANDBOOK_VERSION` | 默认写入 `LineageMeta.handbook_version` |
| `OPS_FORGE_DATA_DIR` | 默认 `data`，预留 |

---

## 6. CLI 速查

| 子命令 | 作用 |
|--------|------|
| `ops-distiller map` | merged → JSONL（+ 可选 SQLite） |
| `ops-distiller episodes` | JSONL → `episodes.json`（Graphiti 批次） |
| `ops-distiller reduce` | 多课 JSONL 粗归并 |
| `ops-distiller export-manifest` | 写 `AgentManifestV1` JSON |
| `ops-distiller eval-recall` | 关键词 recall baseline |

---

## 7. 与 ③ 的衔接（实施清单）

1. **Graphiti**：使用本仓产出的 `episodes.json`，在运维环境执行 `ops-agent graphiti-ingest`（需 Neo4j + OpenAI）。**`group_id`** 与运行时 `sanitize_group_id(client_id)` 一致。
2. **Manifest**：将 `export-manifest` 输出路径设为 ③ 的环境变量 **`OPS_AGENT_MANIFEST_PATH`**（由 `ops-agent` 加载，见该仓库 `manifest_loader.py`）；工具仍由 ③ 代码注册，JSON 仅含工具名字符串列表。

---

## 8. 路线图

| 阶段 | 内容 |
|------|------|
| **0.1（当前）** | Map 确定性 + DSPy 可选；投影；Manifest；SQLite/JSONL；评测 baseline |
| **0.2** | Reduce 真实聚簇；Ground Truth 列表 + Embedding + LLM Judge |
| **0.3** | TurboGEPA 与 DSPy `compile` 集成；CI 中阈值门禁 |

---

## 9. 变更流程

1. 行为或契约变化 → 更新 **ENGINEERING.md** 与 **CHANGELOG.md**。  
2. 跨仓环境变量 → 更新 **CONTRACT.md** 与根 **PIPELINE.md**。  
3. 版本号：`pyproject.toml` 与 `ops_distiller_forge.__version__` 对齐。
