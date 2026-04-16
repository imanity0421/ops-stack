# ops-distiller-forge

**② 阶段炼金工坊**：在 **① `lesson_merged.json`** 与 **③ `ops-agent`** 之间，提供基于 **Pydantic 母版** 的知识蒸馏、**Graphiti Episode** 投影、**Agent Manifest** 导出；可选 **DSPy** 与后续 GEPA/评测扩展。

## 权威文档

| 文档 | 内容 |
|------|------|
| [docs/ENGINEERING.md](docs/ENGINEERING.md) | **工程方案依据**：边界、架构、类型、CLI、路线图 |
| [docs/CONTRACT.md](docs/CONTRACT.md) | 与 ①③ 的契约与产物 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本变更 |
| [../PIPELINE.md](../PIPELINE.md)（工作区根） | ①→②→③ 总览 |

## 安装

```bash
cd ops-distiller-forge
pip install -e ".[dev]"
copy .env.example .env   # 使用 DSPy 时配置 OPENAI_API_KEY
# 可选：DSPy 蒸馏
# pip install -e ".[dspy]"
```

## 快速开始（无 API）

```bash
# 假设已有 lesson_merged.json
ops-distiller map path/to/lesson_merged.json -o data/knowledge_points.jsonl
ops-distiller episodes --jsonl data/knowledge_points.jsonl -o out/episodes.json
ops-distiller export-manifest -o out/agent_config.json --handbook-version 0.1.0 --system-prompt "你是私域运营顾问..."
```

使用 **DSPy + LLM**：

```bash
export OPENAI_API_KEY=...
ops-distiller map path/to/lesson_merged.json -o data/kp.jsonl --use-dspy
```

将 `out/episodes.json` 交给 **`ops-agent graphiti-ingest`**（需 Neo4j）入库；将 `agent_config.json` 交给 ③ 的 Loader（实现中）。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
