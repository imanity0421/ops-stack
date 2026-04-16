# ① → ② → ③ 管线总览（`ops-stack`）

**定位**：本文档是 **命令与环境变量速查**；业务背景、目录说明全文与「新机 checklist」见 [**PROJECT_CONTEXT.md**](PROJECT_CONTEXT.md)。

本文件描述 **`ops-stack/`** 总目录下各子工程如何衔接；**不要求**已跑通全流程即可先做校验、清单与本地 Agent。  
**子目录名**以 **`ops-stack.toml`** 为准，下表为默认名称（与 PyPI 包名一致）。

## 仓库与职责

| 阶段 | 默认目录 | 职责 |
|------|----------|------|
| ① | **`video-raw-ingest/`** | 视频 → `lesson_merged.json` / `validation_report.json` |
| ②a | **`ops-knowledge/`** | 轻量衔接：校验 merged、`handbook_handoff.json`、**`dspy-stub`** 占位（无 LLM） |
| ②b | **`ops-distiller-forge/`** | 炼金工坊：DSPy + Pydantic 母版、Episode 投影、Manifest、`eval-recall`；见该目录 `docs/ENGINEERING.md` |
| ③ | **`ops-agent/`** | 运行时 Agent；`doctor` / Mem0 / Hindsight / Graphiti 只读；探针、规则评测；离线 `graphiti-ingest`；**`OPS_AGENT_MANIFEST_PATH`** |

## 建议命令（开发机）

路径以 **`ops-stack`** 为当前目录（例：`D:\Coding\ops-stack`）。

```powershell
# ① 产出略；假设已有 out_dir\lesson_merged.json

# ②a 校验与清单（需 tests/fixtures 或 VIDEO_RAW_INGEST_ROOT 或 --schema）
cd ops-knowledge
pip install -e ".[dev]"
ops-knowledge validate D:\path\to\lesson_merged.json
ops-knowledge manifest --ingest-root D:\path\to\batch_out -o D:\path\to\handbook_handoff.json

# ②a 占位蒸馏（无 LLM，联调用）
ops-knowledge dspy-stub D:\path\to\lesson_merged.json -o D:\path\to\distill_stub.json

# ③ 自检与辅助命令
cd ..\ops-agent
pip install -e ".[dev]"
pip install -e ".[graphiti]"   # 可选：Graphiti 只读
ops-agent doctor --strict

ops-agent eval tests\fixtures\e2e_eval_case.json
ops-agent knowledge-append-jsonl -o data\knowledge.jsonl --client-id demo_client --text "示例知识点"
# ops-agent mcp-probe-server   # 需 pip install -e ".[mcp]"
```

## 环境变量（衔接）

| 变量 | 用途 |
|------|------|
| `VIDEO_RAW_INGEST_ROOT` | 指向 **①** 仓库根（默认目录名见 `ops-stack.toml` 中 `video_raw_ingest`），供 `ops-knowledge` 定位 `schema/lesson_merged.schema.json` |
| `OPS_HANDOFF_MANIFEST_PATH` | `handbook_handoff.json`；**运行时**注入 `get_agent` 指令摘要 |
| `OPS_AGENT_MANIFEST_PATH` | **②b** `export-manifest` 产出的 JSON；注入 `system_prompt`、筛选工具 |
| `OPS_KNOWLEDGE_FALLBACK_PATH` | 无 Neo4j 时领域知识 JSONL 降级 |
| `OPS_GOLDEN_RULES_PATH` | 交付规则 JSON；工具 `check_delivery_text` |
| `OPS_MCP_PROBE_FIXTURE_PATH` | 探针 JSON；工具 `fetch_ops_probe_context` |

详见：`ops-knowledge/docs/CONTRACT.md`、`ops-agent/docs/ENGINEERING.md`、`ops-agent/docs/OPERATIONS.md`。

**② 分工**：**②a** 零/轻依赖、可 CI；**②b** 重依赖蒸馏与炼金，与 ③ 运行时解耦。制品经环境变量指向 ③。

## 模拟端到端（无真实课数据）

**`pipeline-demo/`**：在 **`ops-stack`** 根下执行：

```powershell
python pipeline-demo\run_e2e_demo.py
```

生成 `pipeline-demo/out/`（`handbook_handoff.json`、`agent_config.json`、降级 JSONL、`env_snippet.ps1`）。详见 **`pipeline-demo/README.md`**。

## 离线 Graphiti 写入（可选）

在 **③** 中使用：

- `ops-agent graphiti-ingest docs/examples/graphiti_episodes.example.json --dry-run`

## 版本参考（见各 `pyproject.toml`）

| 包 | 说明 |
|----|------|
| `ops-agent` | 0.5.x |
| `ops-knowledge` | 0.1.3+ |
| `ops-distiller-forge` | 0.1.x |
