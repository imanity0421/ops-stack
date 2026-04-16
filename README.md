# ops-stack — 私域运营管线总工程

本目录聚合 **① 原始数据**、**② 知识加工**（②a 轻量衔接 + ②b 炼金工坊）、**③ 运行时 Agent** 及端到端演示脚本。

**子目录名**由 **`ops-stack.toml`** 集中配置；请勿在业务代码中写死文件夹名（`pipeline-demo`、`ops-knowledge` 测试已读取该配置）。

## 目录一览

| 路径 | 阶段 | 说明 |
|------|------|------|
| [`video-raw-ingest/`](video-raw-ingest/) | ① | 视频转写与 `lesson_merged.json` |
| [`ops-knowledge/`](ops-knowledge/) | ②a | 校验、`handbook_handoff`、`dspy-stub` |
| [`ops-distiller-forge/`](ops-distiller-forge/) | ②b | 蒸馏、Manifest、Episode 投影 |
| [`ops-agent/`](ops-agent/) | ③ | Agno 专项运营 Agent |
| [`pipeline-demo/`](pipeline-demo/) | 串联 | 模拟数据 `run_e2e_demo.py` |

## 文档

| 文件 | 内容 |
|------|------|
| [**PROJECT_CONTEXT.md**](PROJECT_CONTEXT.md) | **项目背景、技术栈、结构、完成度与迁移清单（新机器必读）** |
| [**PIPELINE.md**](PIPELINE.md) | ①→②→③ 命令、环境变量 |
| [**NAMING.md**](NAMING.md) | 目录与 `ops-stack.toml` 约定 |

## 本地安装（开发）

在 **`ops-stack`** 根目录下对各子项目执行可编辑安装，例如：

```powershell
cd ops-knowledge
pip install -e ".[dev]"
cd ..\ops-distiller-forge
pip install -e ".[dev]"
cd ..\ops-agent
pip install -e ".[dev]"
```

端到端演示：

```powershell
cd D:\path\to\ops-stack
python pipeline-demo\run_e2e_demo.py
```

## 许可证

各子项目自有 `LICENSE`；以子项目为准。
