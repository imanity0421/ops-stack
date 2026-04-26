# agent-os-runtime

Agent OS Runtime：**Agno + Mem0 + Hindsight + Graphiti（只读）+ AsyncReview**；含 **handoff 注入**、**Golden rules**、**MCP 探针 fixture**、**端到端规则评测 CLI** 等（版本见 **`pyproject.toml`** / `CHANGELOG.md`）。

## 独立研发 / Cursor Agent（合回 monorepo 前必读）

| 文档 | 内容 |
|------|------|
| [**AGENTS.md**](AGENTS.md) | **跨机器与独立拷贝研发时的约束与自检清单**（勿破坏与 ①② 的契约、勿引入兄弟包 import） |

## 文档与阅读顺序（人读 / AI 读）

**只要一张表**：从上一行 `AGENTS.md` 看红线后，按优先级读下面文档即可；**不必**在 `docs/` 下再找第二个 README（已合并到本文件，避免与根目录重复）。

| 优先级 | 文档 | 作用 |
|--------|------|------|
| 1 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 数据流、读写路径、检索顺序（短） |
| 2 | [docs/ENGINEERING.md](docs/ENGINEERING.md) | **主权威**：模块边界、目录、契约、环境变量、ADR |
| 3 | [docs/OPERATIONS.md](docs/OPERATIONS.md) | 环境变量**全表**、排障、`doctor` |
| 4 | [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本与行为变化 |
| 5 | [docs/MEMORY_SYSTEM_V2.md](docs/MEMORY_SYSTEM_V2.md) | 四层记忆（**已精简约读版**；未完结的 P3/P4 在文末） |
| 可选 | [docs/AGENT_OS_ROADMAP.md](docs/AGENT_OS_ROADMAP.md) | Sprint/DoD 级规划（**与实现可能部分不同步，以代码为准**） |
| 按需 | [docs/ASSET_STORE.md](docs/ASSET_STORE.md)、[docs/DATA_BACKUP.md](docs/DATA_BACKUP.md)、[docs/examples/](docs/examples/) | 案例库、备份、ingest/合宪样例 |
| 外仓无 | [../PIPELINE.md](../PIPELINE.md) | **仅**本目录在 `ops-stack` 内时存在：①→②→③ 总管线；独立检出忽略 |

**独立成仓时**：`src` **不** import 父仓库。①② `video-raw-ingest` / `ops-knowledge` 为**可选上游**，经 env/文件对接，**非**运行时硬依赖。

**给助手的 3 行**：`get_agent` / `get_reasoning_agent` + `Settings.from_env`；`MemoryController` 为推荐写入口；Graphiti 在 Agent 路径**只读**；Hindsight = JSONL **append-only**，治理在**召回**（`HindsightRetrievalPolicy`，`supersedes` = 对旧行**降权**）。

## 快速开始

```bash
cd agent-os-runtime
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"
pre-commit install               # 可选：git commit 前跑 Ruff；无 PATH 时用: python -m pre_commit install（见 OPERATIONS.md）
pip install -e ".[graphiti]"   # 可选：Graphiti 只读
# pip install -e ".[mcp]"     # 可选：MCP 探针 stdio 服务
copy .env.example .env
# 编辑 .env：OPENAI_API_KEY；可选 MEM0_API_KEY、NEO4J_*、AGENT_OS_KNOWLEDGE_FALLBACK_PATH、AGENT_OS_HANDOFF_MANIFEST_PATH、AGENT_OS_MANIFEST_DIR、AGENT_OS_GOLDEN_RULES_PATH、AGENT_OS_MCP_PROBE_FIXTURE_PATH
python -m agent_os --client-id demo_client
```

未配置 `MEM0_API_KEY` 时，记忆自动落盘到 `data/local_memory.json`（见 OPERATIONS.md）。未配置 Neo4j 时，领域知识工具会尝试 JSONL 降级或返回配置提示。

**自检**：`agent-os-runtime doctor`；严格模式：`agent-os-runtime doctor --strict`（缺少 `OPENAI_API_KEY` 时非零退出）。

**其它子命令**（节选，完整见 [OPERATIONS.md](docs/OPERATIONS.md)）：

| 命令 | 说明 |
|------|------|
| `agent-os-runtime eval <case.json>` | 端到端规则评测（Golden rules，无 LLM） |
| `agent-os-runtime knowledge-append-jsonl --output ... --client-id ... [--skill default_agent] --text ...` | 追加 JSONL 领域知识（无需 Neo4j） |
| `agent-os-runtime graphiti-ingest <episodes.json> [--dry-run]` | 离线 Graphiti 写入（实跑需 Neo4j + OpenAI） |
| `agent-os-runtime mcp-probe-server` | stdio MCP 探针（需 `[mcp]`） |

### 浏览器试用（本地 Web，无鉴权示例）

```bash
pip install -e ".[web]"
python examples/web_chat_fastapi.py
```

浏览器打开终端里提示的地址：**对话**默认 `http://127.0.0.1:8765/`；**记忆管理**（画像、Hindsight、手动写入）为 `http://127.0.0.1:8765/memory`。对话页含：**回复下方展开「执行与思考过程」**（Agno `RunOutput` 的 reasoning / tools / metrics 等）、**结束对话**及可选 **AsyncReview 复盘**；身份可在页内切换多组 **client_id / user_id**（localStorage 预设）。详见 `examples/web_chat_fastapi.py`；生产环境请自建鉴权与 HTTPS，勿直接暴露公网。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
