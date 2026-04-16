# ① → ② → ③ 契约（跨仓库）

## 数据流

```
video-raw-ingest (①，目录名见 ops-stack/ops-stack.toml)
  lesson_merged.json / validation_report.json
        ↓
ops-knowledge validate / manifest / dspy-stub（本包，②a 轻量）
  handbook_handoff.json、distill_stub.json（占位）
        ↓
ops-distiller-forge（②b）：DSPy 真蒸馏、Pydantic 母版、手册、Episode 投影、Metric/GEPA、agent_config.json
  （Graphiti 入库：投影 Episode 后调 add_episode；可选用 ops-agent graphiti-ingest 或 forge 内批处理）
        ↓
ops-agent（③）运行时
```

## ① 产出

- 主文件：`lesson_merged.json`，Schema：`schema_version == "1.0"`（见 **`video-raw-ingest`** 内 `schema/lesson_merged.schema.json`）。

## ② 本包职责

- **`ops-knowledge validate`**：对 `lesson_merged.json` 做 JSON Schema 校验。
- **`ops-knowledge manifest`**：扫描目录，汇总每课的校验结果与文件 SHA256，写入 **`handbook_handoff.json`**。
- **`ops-knowledge dspy-stub`**：无 LLM，从单课 merged 生成 `distill_stub.json`（占位，供管线联调；真蒸馏在 DSPy 工程）。

### Schema 路径解析顺序

1. 环境变量 **`VIDEO_RAW_INGEST_ROOT`**：指向 **①** 仓库根目录（默认目录名见 **`ops-stack.toml`** 中 `video_raw_ingest`），则使用  
   `{VIDEO_RAW_INGEST_ROOT}/schema/lesson_merged.schema.json`
2. **`--schema`** 显式指定 schema 文件路径。

## ③ ops-agent 可选衔接

- 可在部署时设置 **`OPS_HANDOFF_MANIFEST_PATH`** 指向 `handbook_handoff.json`（运行时注入 Agent 指令摘要；**不替代** Graphiti 检索）。
- 可选 **`OPS_GOLDEN_RULES_PATH`**、**`OPS_MCP_PROBE_FIXTURE_PATH`**、**`OPS_KNOWLEDGE_FALLBACK_PATH`** 等，见 `ops-agent/docs/OPERATIONS.md`。
- 离线 Graphiti `add_episode`：**不**在运行时 API 暴露；使用 **`ops-agent graphiti-ingest`**（需 Neo4j + OpenAI）与只读检索解耦。

## handbook_handoff.json 字段（摘要）

| 字段 | 说明 |
|------|------|
| `handoff_version` | 本清单格式版本 |
| `created_utc` | ISO 时间 |
| `video_raw_ingest_schema_ref` | 校验所用的 schema 路径 |
| `lessons` | 每课：`relpath`、`sha256`、`valid`、`errors` |
