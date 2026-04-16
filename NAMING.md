# ops-stack 目录与包名约定

本文件说明 **`ops-stack/`** 总工程内各子仓库的**磁盘目录名**与 **Python 包名 / CLI** 的关系。

## 1. 单点配置：改目录名不必改代码

- **`ops-stack.toml`**（与本文件同目录）中的 **`[repos]`** 声明各子仓库**相对于 `ops-stack` 根**的文件夹名。
- 若你重命名了某个子文件夹，只需同步修改 **`ops-stack.toml`**（或设置环境变量 **`OPS_STACK_REPO_<KEY>`**，KEY 为 `video_raw_ingest`、`ops_knowledge` 等的大写下划线形式，例如 **`OPS_STACK_REPO_OPS_KNOWLEDGE=my-ops-knowledge`**）。
- **`pipeline-demo/run_e2e_demo.py`** 与 **`ops-knowledge/tests/conftest.py`** 通过 **`load_layout.py`** 读取上述配置，**不再写死**子目录名。

**仍可能受重命名影响的环节**：

- 你在 shell 里手写的 `cd` 路径、IDE 工作区、**可编辑安装**（`pip install -e`）指向的旧路径——需在重命名后重新执行 `pip install -e .`。
- **`VIDEO_RAW_INGEST_ROOT`**、**`OPS_*`** 等若写死绝对路径，需自行更新。

## 2. 默认目录名（与 PyPI 包名对齐）

| 管线角色 | 默认目录名 | `pyproject` name | import 示例 | CLI 示例 |
|----------|------------|-------------------|-------------|----------|
| ① 原始数据 | **`video-raw-ingest`** | `video-raw-ingest` | `video_raw_ingest` | `video-raw-ingest` |
| ②a 衔接 | **`ops-knowledge`** | `ops-knowledge` | `ops_knowledge` | `ops-knowledge` |
| ②b 炼金 | **`ops-distiller-forge`** | `ops-distiller-forge` | `ops_distiller_forge` | `ops-distiller` |
| ③ 运行时 | **`ops-agent`** | `ops-agent` | `ops_agent` | `ops-agent` |
| 串联演示 | **`pipeline-demo`** | （非独立包） | — | — |

**规则**：目录名采用 **kebab-case**；**包名、import、CLI 不随你改磁盘文件夹名而变**（除非你改 `pyproject.toml`，不推荐）。

## 3. 环境变量（与目录解耦）

| 变量 | 用途 |
|------|------|
| `OPS_*` | ③ 与跨仓衔接（`OPS_HANDOFF_MANIFEST_PATH`、`OPS_AGENT_MANIFEST_PATH` 等） |
| `OPS_FORGE_*` | ②b 工坊可选配置 |
| `VIDEO_RAW_INGEST_ROOT` | 指向 **①** 仓库根，供 `ops-knowledge` 定位 `schema/lesson_merged.schema.json` |
| `OPS_STACK_REPO_*` | 覆盖 **`ops-stack.toml`** 中对应 `repos` 键的目录名 |

## 4. 文档索引

- 管线命令摘要：`PIPELINE.md`
- 项目全景与迁移说明：**`PROJECT_CONTEXT.md`**
- 总入口：`README.md`
