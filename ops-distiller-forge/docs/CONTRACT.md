# 跨仓库契约（① / ②b / ③）

## 输入（来自 ①）

- 文件：`lesson_merged.json`，`schema_version == "1.0"`（见 **`video-raw-ingest`** 内 `schema/lesson_merged.schema.json`；目录名以 `ops-stack.toml` 为准）。
- 本仓 **不** 校验 schema；校验请用 **`ops-knowledge validate`**。

## 输出（②b → ③）

| 产物 | 说明 |
|------|------|
| **KnowledgePoint JSONL** | 每行一个 JSON，`KnowledgePoint.model_dump()` |
| **episodes.json** | `EpisodeBatchFile`，供 `ops-agent graphiti-ingest` |
| **agent_config.json** | `AgentManifestV1`，供 ③ Loader（待 `ops-agent` 实现加载） |
| **handbook 渲染** | 后续版本：由 `KnowledgePoint` 拼装 Markdown/HTML；当前可先用手册外置生成器 |

## 环境变量（与 ③ 对齐）

- `OPS_HANDBOOK_VERSION`：与 `AgentManifestV1.handbook_version`、溯源元数据一致时，③ 可统一展示「当前手册版本」。
- Graphiti 运行时仍使用 ③ 已有变量：`NEO4J_*`、`OPS_KNOWLEDGE_FALLBACK_PATH` 等。

## 与 ops-knowledge（②a）

- **②a** 产出 `handbook_handoff.json`（课级 SHA、校验结果）；**②b** 产出方法论知识点与 Manifest。二者可同时指向同一批次目录，通过 **`source_relpath`** 对齐。
