# 操作手册（ops-agent）

## 环境

- Python 3.10+
- 网络可访问 OpenAI 或兼容 API（若使用中转，配置 `OPENAI_API_BASE`）

## 安装

```bash
cd ops-agent
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -U pip
pip install -e .
# 领域知识（Graphiti，可选）
pip install -e ".[graphiti]"
```

## 配置

复制 `.env.example` 为 `.env`，至少设置：

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | 必需 |
| `OPENAI_API_BASE` | 可选，OpenAI 兼容端点 |
| `MEM0_API_KEY` | 可选；未设置则使用 `data/local_memory.json` |
| `MEM0_HOST` | 可选，默认 Mem0 云 |
| `OPS_AGENT_MODEL` | 可选，默认 `gpt-4o-mini` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 可选；设置后启用 Graphiti 只读检索（需 `[graphiti]`） |
| `OPS_KNOWLEDGE_FALLBACK_PATH` | 可选；JSONL 降级，格式见 `docs/examples/knowledge_fallback.example.jsonl` |
| `OPS_HISTORICAL_PATH` | 可选；Hindsight JSONL 路径，默认 `data/hindsight.jsonl`（兼容 `OPS_HISTORICAL_STUB_PATH`） |
| `OPS_ENABLE_HINDSIGHT` | 默认 `1`；设为 `0` 可完全关闭 Hindsight（不创建 store，不挂载相关工具） |
| `OPS_ASYNC_REVIEW_ON_EXIT` | 默认 `1`；设为 `0` 关闭退出时复盘 |
| `OPS_HANDOFF_MANIFEST_PATH` | 可选；`ops-knowledge manifest` 生成的 `handbook_handoff.json`；**运行时**会摘要注入 `get_agent` 指令 |
| `OPS_AGENT_MANIFEST_DIR` | 可选；扫描其中 **`*.json`** 作为 skill 配方（文件名即 **`skill_id`**）；可与包内置 `default_ops` / `short_video` 合并覆盖 |
| `OPS_AGENT_DEFAULT_SKILL_ID` | 可选；未传 `--skill` / `skill_id` 时的默认 skill（默认 `default_ops`） |
| `OPS_GOLDEN_RULES_PATH` | 可选；JSON 数组正则规则（见 `data/golden_rules.example.json`）；启用工具 `check_delivery_text` |
| `OPS_MCP_PROBE_FIXTURE_PATH` | 可选；覆盖默认探针 JSON（否则使用包内 `mcp_probe_default.json`）；工具 `fetch_ops_probe_context` |
| `OPS_ENABLE_ASSET_STORE` | 可选；启用参考案例库（Asset Store / LanceDB），运行时仅检索 |
| `OPS_ASSET_STORE_PATH` | 可选；Asset Store 的本地路径（LanceDB 目录） |
| `OPS_ENABLE_MEM0_LEARNING` | 默认 `1`；设为 `0` 则不挂载 Mem0 写入工具（仍允许检索画像） |
| `OPS_SKILL_COMPLIANCE_DIR` | 可选；目录下 ``<skill_id>.json`` 为硬合规（与 Golden rules 同格式）；**asset-ingest 入库**与工具 **check_skill_compliance_text** 共用 |
| `OPS_ASSET_NEAR_DEDUP_L2_MAX` | 可选；设置如 `0.18` 时启用**近似去重**（特征向量 L2）；不设置则只做强指纹去重 |
| `VIDEO_RAW_INGEST_ROOT` | 可选；供 `doctor` 检查与 `ops-knowledge` 定位 schema |
| `OPS_GRAPHITI_SEARCH_TIMEOUT_SEC` 等 | 见 [ENGINEERING.md](ENGINEERING.md) §5 |

## 环境与依赖自检

```bash
ops-agent doctor
ops-agent doctor --strict
```

## 运行 CLI

```bash
python -m ops-agent --client-id my_client
```

- `--user-id`：多终端用户时区分。
- `--slow`：启用 Agno 内置 `reasoning`；若报错可去掉该 flag（见排障）。
- `--no-knowledge`：不挂载 `search_domain_knowledge`（仅 Mem0）。
- `--skill`：指定 **`skill_id`**（须存在于 manifest 注册表）；影响系统提示与 **Graphiti `graphiti_group_id(client_id, skill)`**。

## 辅助命令（默认数据 / 离线）

```bash
# 端到端规则评测（仅 Golden rules，无 LLM）
ops-agent eval tests/fixtures/e2e_eval_case.json

# 向 JSONL 降级知识库追加行（无需 Neo4j）
ops-agent knowledge-append-jsonl -o data/knowledge.jsonl --client-id my_client --skill default_ops --text "私域复购的关键是..."

# Graphiti 离线写入：先 dry-run，再在有 NEO4J_* + OPENAI_API_KEY 时实跑
ops-agent graphiti-ingest docs/examples/graphiti_episodes.example.json --dry-run

# 参考案例库：离线导入（需 pip install -e ".[asset_store]"，含 lancedb）
# ops-agent asset-ingest my_case.txt --client-id my_client --skill short_video
# 删除单条或按 tenant+skill 清空（回退垃圾入库）
# ops-agent asset-rm --case-id <uuid>
# ops-agent asset-rm --client-id my_client --skill short_video --all-skill

# MCP 探针 stdio 服务（需 pip install -e ".[mcp]"）
ops-agent mcp-probe-server
```

## 产出文件（本地模式）

| 路径 | 说明 |
|------|------|
| `data/local_memory.json` | 无 Mem0 时的本地记忆 |
| `data/hindsight.jsonl`（`OPS_HISTORICAL_PATH`） | Hindsight：反馈与复盘教训，JSONL |

## 排障

1. **`ImportError` / Agno API 变化**  
   对照 <https://docs.agno.com>，仅修改 `ops_agent/agent/factory.py` 中的构造逻辑。

2. **Mem0 鉴权失败**  
   检查 `MEM0_API_KEY`；或暂时去掉该变量使用本地 JSON。

3. **`reasoning` 相关错误**  
   不使用 `--slow`，或更换支持推理链的模型（以 Agno 文档为准）。

4. **Windows 控制台中文乱码**  
   可设置环境变量 `PYTHONIOENCODING=utf-8`，或使用 Windows Terminal UTF-8 代码页。

5. **`graphiti-core` 未安装**  
   执行 `pip install -e ".[graphiti]"`。

6. **Graphiti 连接失败**  
   检查 Neo4j 是否可达、`group_id` 与入库时是否一致（**`graphiti_group_id(client_id, skill_id)`**）；可临时配置 `OPS_KNOWLEDGE_FALLBACK_PATH` 仅测 Agent 流程。

7. **AsyncReview**  
   退出 CLI 时默认会复盘并写入 `Hindsight` 教训（需 `OPENAI_API_KEY`）。可用 `--no-async-review` 或 `OPS_ASYNC_REVIEW_ON_EXIT=0` 关闭。

## 作为库导入

```python
from ops_agent.config import Settings
from ops_agent.memory.controller import MemoryController
from ops_agent.agent.factory import get_agent
from ops_agent.knowledge.graphiti_reader import GraphitiReadService

settings = Settings.from_env()
ctrl = MemoryController.create_default(
    mem0_api_key=settings.mem0_api_key,
    mem0_host=settings.mem0_host,
    local_memory_path=settings.local_memory_path,
    hindsight_path=settings.hindsight_path,
)
knowledge = GraphitiReadService.from_env(settings.knowledge_fallback_path)
agent = get_agent(
    ctrl,
    client_id="c1",
    user_id="u1",
    thought_mode="fast",
    knowledge=knowledge,
    skill_id=None,  # 默认 Settings.default_skill_id；Graphiti 使用 graphiti_group_id(c1, skill)
)
```
