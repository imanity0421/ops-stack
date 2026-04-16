# ①②③ 模拟端到端演示

使用 **`pipeline-demo/fixtures/lesson_merged.json`** 模拟 ① 产出，串联：

1. **②a** `ops-knowledge`：`validate` → `manifest` → `handbook_handoff.json`
2. **②b** `ops-distiller-forge`：`map` → `knowledge_points.jsonl` → `episodes` → `agent_config.json`
3. **③**：生成 `knowledge_fallback.jsonl` 与 **`env_snippet.ps1`**（`OPS_HANDOFF_MANIFEST_PATH`、`OPS_AGENT_MANIFEST_PATH`、`OPS_KNOWLEDGE_FALLBACK_PATH`）

子目录名以 **`ops-stack/ops-stack.toml`** 为准；脚本 **`run_e2e_demo.py`** 通过 **`load_layout.py`** 解析路径，**勿写死**文件夹名。

## 前置

在 **`ops-stack`** 根目录下已执行：

```powershell
pip install -e ops-knowledge
pip install -e ops-distiller-forge
pip install -e ops-agent
```

## 运行

```powershell
cd D:\path\to\ops-stack
python pipeline-demo\run_e2e_demo.py
```

产物在 **`pipeline-demo/out/`**，按 `env_snippet.ps1` 设置环境变量后启动 **`ops-agent`**（在 **`ops-agent`** 子目录，需 `OPENAI_API_KEY`）。

## Graphiti（可选）

无 Neo4j 时可省略；有则可将 `out/episodes.json` 用于 `ops-agent graphiti-ingest`（先 `--dry-run`）。
