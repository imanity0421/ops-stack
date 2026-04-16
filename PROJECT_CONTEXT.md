# ops-stack 项目全景与续作指南

本文档面向：**将本仓库复制到另一台机器、用 Cursor 打开后继续开发**的同事或未来的你。阅读顺序建议：**本文件 → `README.md` → `PIPELINE.md` → 各子仓 `docs/ENGINEERING.md`**。

---

## 1. 需求背景与目标

### 1.1 业务语境

面向 **私域运营** 场景：从 **课程/视频原始素材** 得到结构化 **`lesson_merged.json`**，再经 **知识加工**（校验、蒸馏、知识点与 Episode 投影、Agent 配方 Manifest），最终在 **③ 运行时** 由 **专项运营顾问 Agent** 结合 **Mem0（客户记忆）**、**Hindsight（任务反馈）**、**Graphiti + Neo4j（领域知识图谱，只读）** 等能力对外服务。

### 1.2 工程目标

- **多仓库分工清晰**：① 采集与合并、②a 轻量衔接、②b 重计算蒸馏、③ 运行时互不拖垮依赖。
- **契约驱动**：`lesson_merged.json` Schema、`handbook_handoff.json`、`AgentManifestV1`、`EpisodeBatchFile` 等与实现文档（各仓 `docs/CONTRACT.md`、`ENGINEERING.md`）对齐。
- **可迁移**：通过 **`ops-stack.toml`** 声明子目录名，避免脚本写死路径；环境相关配置走 **环境变量** 与 **`.env`**。

---

## 2. 逻辑管线（① → ② → ③）

```
video-raw-ingest (①)
  lesson_merged.json / validation_report.json
        ↓
ops-knowledge（②a）validate / manifest / dspy-stub
  handbook_handoff.json 等
        ↓
ops-distiller-forge（②b）map → reduce(占位) → episodes → export-manifest
  knowledge_points.jsonl、episodes.json、agent_config.json
        ↓
ops-agent（③）Loader + CLI + Agno/Mem0/Hindsight/Graphiti 只读
```

**模拟端到端**：`ops-stack` 根目录执行 `python pipeline-demo/run_e2e_demo.py`，产物在 `pipeline-demo/out/`。

---

## 3. 技术选型摘要

| 层次 | 技术 |
|------|------|
| 语言与版本 | Python **≥ 3.10**（推荐 3.11+，`tomllib` 解析 `ops-stack.toml`） |
| ① 原始数据 | 各模块见 `video-raw-ingest` 内文档；CLI 包名 `video-raw-ingest` |
| ②a | **JSON Schema** 校验（`jsonschema`）、**Pydantic**；CLI **`ops-knowledge`** |
| ②b | **Pydantic** Ontology、可选 **DSPy**（`[dspy]` extra）、JSONL/SQLite 存储；CLI **`ops-distiller`** |
| ③ | **Agno** 编排、**Mem0**、**Neo4j + Graphiti**（只读检索，可选 `[graphiti]`）、自研 **Hindsight** / **AsyncReview**；CLI **`ops-agent`** |
| 测试 | **pytest**（各子仓独立） |

---

## 4. 物理结构与配置

### 4.1 总目录 `ops-stack`

| 路径 | 角色 |
|------|------|
| **`ops-stack.toml`** | **[repos]**：各子仓相对 `ops-stack` 的文件夹名（**改目录名主要改此文件**） |
| **`load_layout.py`** | 读取 TOML + 环境变量 `OPS_STACK_REPO_*`，供 demo 与测试解析路径 |
| **`video-raw-ingest/`** | ① |
| **`ops-knowledge/`** | ②a |
| **`ops-distiller-forge/`** | ②b |
| **`ops-agent/`** | ③ |
| **`pipeline-demo/`** | 串联脚本与 fixture，非独立 Python 包 |

### 4.2 目录名与包名

- **磁盘目录名**可与历史习惯不同；**Python 包名 / pip 包名 / CLI** 定义在各子目录 **`pyproject.toml`**，**不因你改文件夹名而自动变**。
- 若重命名子文件夹：编辑 **`ops-stack.toml`**，或设置 **`OPS_STACK_REPO_<KEY>`**（见 `load_layout.py` 内注释）。

### 4.3 跨仓衔接环境变量（节选）

| 变量 | 含义 |
|------|------|
| `VIDEO_RAW_INGEST_ROOT` | ① 仓库根，供 `ops-knowledge` 找 `schema/lesson_merged.schema.json` |
| `OPS_HANDOFF_MANIFEST_PATH` | `handbook_handoff.json` |
| `OPS_AGENT_MANIFEST_PATH` | ②b 导出的 Agent 配方 JSON |
| `OPS_KNOWLEDGE_FALLBACK_PATH` | 无 Neo4j 时的 JSONL 降级知识 |
| `OPENAI_API_KEY` | ③ 及可选 LLM 路径必需 |

完整列表见 **`PIPELINE.md`** 与 **`ops-agent/docs/OPERATIONS.md`**。

---

## 5. 各子仓文档入口

| 子仓 | 优先阅读 |
|------|----------|
| `video-raw-ingest` | `README.md`、`docs/OPERATIONS.md`、`docs/ENGINEERING.md` |
| `ops-knowledge` | `README.md`、`docs/CONTRACT.md` |
| `ops-distiller-forge` | `docs/ENGINEERING.md`、`docs/CONTRACT.md` |
| `ops-agent` | `README.md`、`docs/ENGINEERING.md`、`docs/OPERATIONS.md`、`docs/ARCHITECTURE.md` |

---

## 6. 已完成部分（实现要点）

| 模块 | 状态 | 说明 |
|------|------|------|
| ① `video-raw-ingest` | 可用 | 视频链路与 `lesson_merged` 产出（细节以该仓文档为准） |
| ②a `ops-knowledge` | 可用 | `validate`、`manifest`、`dspy-stub`；测试覆盖 |
| ②b `ops-distiller-forge` | 可用 | `map`、占位 `reduce`、`episodes`、`export-manifest`、`eval-recall` 等；可选 DSPy |
| ③ `ops-agent` | 可用 | `doctor`、主 Agent CLI、Mem0/Hindsight、Graphiti 只读工具链、规则评测、`graphiti-ingest` 离线写入等 |
| `pipeline-demo` | 可用 | `run_e2e_demo.py` 串联 ②a→②b→环境变量片段 |
| `ops-stack.toml` + `load_layout.py` | 可用 | 子目录名可配置，避免脚本写死路径 |

---

## 7. 未完成或后续可增强（备注）

| 项 | 说明 |
|----|------|
| ②b **reduce** | 当前为占位/粗策略；后续可接 embedding、聚类、人工 gate（见 `ENGINEERING.md`） |
| **手册 HTML/Markdown 渲染** | 规划中；当前以 JSONL/Episode/Manifest 为主 |
| **多租户与商业化** | 明确排除在 ③ 当前范围外（见 `ops-agent/docs/ENGINEERING.md`） |
| **CI 统一** | 各子仓可独立 CI；总仓未强制 monorepo 流水线 |
| **虚拟环境路径** | 移动机器或重命名目录后，在**各子仓**重新 `pip install -e .`，避免 editable 仍指向旧绝对路径 |

---

## 8. 新机器最小 checklist

1. 安装 **Python 3.10+**，建议新建各子仓独立 venv 或单一 venv 内多次 editable 安装。
2. 将整个 **`ops-stack`** 目录复制到目标路径（保持内部相对结构）。
3. 按需编辑 **`ops-stack.toml`**（若你改了子文件夹名）。
4. 在各子目录执行：`pip install -e ".[dev]"`（及 `ops-agent` 的 `[graphiti]` 等可选 extra）。
5. 复制 **`ops-agent/.env.example`** → **`.env`**，填写 `OPENAI_API_KEY` 等。
6. 运行 **`python pipeline-demo/run_e2e_demo.py`**（在 `ops-stack` 根目录）验证 ②a/②b 串联。
7. 运行各子仓 **`pytest`**；运行 **`ops-agent doctor`**。

---

## 9. 版本快照（写入日期参考）

| 包 | 版本（见 `pyproject.toml`） |
|----|-----------------------------|
| `ops-agent` | 0.5.0 |
| `ops-knowledge` | 0.1.3 |
| `ops-distiller-forge` | 0.1.0 |

升级版本时请同步更新各仓 **`CHANGELOG.md`** 与上表。

---

## 10. 与 Cursor 协作建议

- 将 **`ops-stack`** 作为工作区根打开，便于跨子仓搜索与 `ops-stack.toml` 同屏编辑。
- 大改动前先读对应子仓 **`docs/ENGINEERING.md`** 与 **`CONTRACT.md`**，避免破坏 JSON Schema 或 Manifest 字段约定。
- 本文档 **`PROJECT_CONTEXT.md`** 为「鸟瞰」；细节以代码与各子仓文档为准。
