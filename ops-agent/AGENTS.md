# ops-agent 研发约束（供 Cursor / 其它机器上的 Agent 阅读）

本文档约束：**在单独复制本目录、独立仓库或分支中强化 ops-agent 后，合回 `ops-stack` 或其它 monorepo 时应遵守的边界**，以减少联调断裂与重复劳动。

权威细节仍以 **`docs/ENGINEERING.md`**、**`docs/OPERATIONS.md`**、**`docs/CHANGELOG.md`** 为准；冲突时以代码与上述文档为准。

---

## 1. 定位与依赖边界

1. **`ops-agent` 是运行时终点（③）**：可单独拷贝本目录研发；**禁止**在 `src/ops_agent/` 内新增对 **`ops-knowledge`、`ops-distiller-forge`、`video_raw_ingest`** 等 Python 包的 `import` 或进程内耦合。与上游的衔接只能通过 **环境变量**、**JSON 文件路径**、**HTTP/API**。
2. **包与 CLI 标识保持稳定**（合回 monorepo 时强烈建议不改）：
   - `pyproject.toml` 中 `name = "ops-agent"`；
   - 包目录名 **`ops_agent`**（import 根）；
   - 控制台入口 **`ops-agent`** / **`python -m ops_agent`**。  
   若必须改名，须同步修改 **`ops-stack`** 内所有文档、`pipeline-demo`、以及用户脚本，并视为 **破坏性变更**。

---

## 2. 对外契约（合回前尽量不要破坏）

以下被 **`ops-knowledge` / `ops-distiller-forge` / `pipeline-demo` / 运维脚本** 间接依赖，变更前需评估并写 **`CHANGELOG.md`**：

| 类别 | 约束 |
|------|------|
| **环境变量名** | 保持现有 `OPS_*`、`OPENAI_*`、`NEO4J_*`、`MEM0_*` 等语义；重命名旧变量须保留兼容期或文档迁移说明（见 `config.py` / `OPERATIONS.md`）。 |
| **`OPS_AGENT_MANIFEST_PATH`** | 指向的 JSON 须仍能被 **`manifest_loader.AgentManifestV1`** 解析；与 **`ops-distiller-forge`** 的 `export-manifest` 字段对齐。扩展字段可增，**勿随意改必填语义或删字段**。 |
| **`OPS_HANDOFF_MANIFEST_PATH`** | `handbook_handoff.json` 中 **`video_raw_ingest_schema_ref`** 等字段仅作文本/展示用；勿假设本包会加载 ① 的代码。 |
| **`client_id` / `user_id`** | 所有记忆与检索工具必须继续支持租户隔离（见 **ENGINEERING.md §6**）。 |
| **工厂函数** | 外部集成优先依赖 **`ops_agent.agent.factory.get_agent` / `get_reasoning_agent`** 及 **`Settings.from_env()`**；签名变更视为公共 API 变更。 |
| **`get_agent(..., exclude_tool_names=...)`** | Web 演示等调用方依赖；删除或改名参数须同步更新 **`examples/web_chat_fastapi.py`** 与相关测试。 |

---

## 3. 架构红线（与设计决策一致）

1. **Graphiti / Neo4j**：运行时 **只读**（`search_`）；**禁止**在 Agent 运行路径中调用 **`add_episode`** 等写入 API。离线写入仅保留在 **`graphiti-ingest` CLI** 等明确离线入口（见 **ENGINEERING.md**）。
2. **记忆写入**：推荐经 **`MemoryController`** 与既有工具语义；不要绕过租户键向 Mem0/Hindsight 乱写。
3. **资源路径**：包内资源使用相对 **`ops_agent` 包** 的路径（如 `Path(__file__)`），**不要**写死依赖 **`ops-stack` 仓库根** 的相对路径（`examples/` 下脚本以自身目录定位 `.env` 为例外，见 `web_chat_fastapi.py`）。

---

## 4. 人设与可选能力

- **`OPS_AGENT_PERSONA`**：仅允许约定值（如 `ops` / `short_video`），见 **`config.py`**；新增 persona 须在 **ENGINEERING / OPERATIONS** 中说明并考虑 **manifest** 与工具列表兼容性。
- **可选 extras**：`[graphiti]`、`[mcp]`、`[web]`；新增 extra 时更新 **`README.md`** 与 **`pyproject.toml`**，避免默认安装变重。

---

## 5. 合回 monorepo 前自检清单

在将修改后的 **`ops-agent/`** 目录替换回 **`ops-stack/ops-agent/`** 之前建议完成：

1. **`pytest`**（在 `ops-agent` 根，已 `pip install -e ".[dev]"`）。
2. **`ops-agent doctor`**（可选 **`--strict`**，视 CI 要求）。
3. 若改了用户可见行为或契约：**`docs/CHANGELOG.md`** 已更新，**`pyproject.toml` 的 `version`** 与 **`ops_agent.__version__`**（若存在）一致策略。
4. 若改了环境变量或安装步骤：**`docs/OPERATIONS.md`**（及必要时 **ENGINEERING.md**）已更新。
5. 未将 **`.env`**、**`.venv`**、**`data/*.json`** 等本地机密或缓存提交进 Git。

---

## 6. 文档与链接

- **独立仓库**中 `README.md` 里的 **`../PIPELINE.md`** 可能失效：可改为指向 monorepo 的 **绝对 URL**，或注明「仅 monorepo 内有效」。
- 架构与边界以 **`docs/ENGINEERING.md`** 为准；操作与变量表以 **`docs/OPERATIONS.md`** 为准。

---

## 7. 与 `coding-sync`（若仍在 monorepo 内协作）

在 **`ops-stack`** 内开发时，换机/收工可按仓库根 **`coding-sync/README.md`** 追加会话记录；**仅改 `ops-agent` 时**仍应遵守本文件约束，便于他人合回同一目录树。
