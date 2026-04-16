# ops-agent

专项运营智能体运行时：**Agno + Mem0 + Hindsight + Graphiti（只读）+ AsyncReview**；含 **handoff 注入**、**Golden rules**、**MCP 探针 fixture**、**端到端规则评测 CLI** 等（当前 **0.4.x**，见 `CHANGELOG.md`）。

## 权威文档

| 文档 | 内容 |
|------|------|
| [docs/ENGINEERING.md](docs/ENGINEERING.md) | **工程方案依据**：边界、架构、目录、契约、阶段规划、设计决策 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构总览与数据流（简图） |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 安装、环境变量、本地运行与排障 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本变更 |
| [../PIPELINE.md](../PIPELINE.md)（`ops-stack` 根） | ①→②→③ 管线与命令摘要 |

## 快速开始

```bash
cd ops-agent
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"
pip install -e ".[graphiti]"   # 可选：Graphiti 只读
# pip install -e ".[mcp]"     # 可选：MCP 探针 stdio 服务
copy .env.example .env
# 编辑 .env：OPENAI_API_KEY；可选 MEM0_API_KEY、NEO4J_*、OPS_KNOWLEDGE_FALLBACK_PATH、OPS_HANDOFF_MANIFEST_PATH、OPS_GOLDEN_RULES_PATH、OPS_MCP_PROBE_FIXTURE_PATH
python -m ops_agent --client-id demo_client
```

未配置 `MEM0_API_KEY` 时，记忆自动落盘到 `data/local_memory.json`（见 OPERATIONS.md）。未配置 Neo4j 时，领域知识工具会尝试 JSONL 降级或返回配置提示。

**自检**：`ops-agent doctor`；严格模式：`ops-agent doctor --strict`（缺少 `OPENAI_API_KEY` 时非零退出）。

**其它子命令**（节选，完整见 [OPERATIONS.md](docs/OPERATIONS.md)）：

| 命令 | 说明 |
|------|------|
| `ops-agent eval <case.json>` | 端到端规则评测（Golden rules，无 LLM） |
| `ops-agent knowledge-append-jsonl --output ... --client-id ... --text ...` | 追加 JSONL 领域知识（无需 Neo4j） |
| `ops-agent graphiti-ingest <episodes.json> [--dry-run]` | 离线 Graphiti 写入（实跑需 Neo4j + OpenAI） |
| `ops-agent mcp-probe-server` | stdio MCP 探针（需 `[mcp]`） |

## 许可证

MIT — 见 [LICENSE](LICENSE)。
