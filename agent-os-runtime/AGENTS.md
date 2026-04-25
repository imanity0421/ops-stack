# agent-os-runtime 研发约束（供 Cursor / 其它机器上的 Agent 阅读）

本文档约束：**在单独复制本目录、独立仓库或分支中强化 agent-os-runtime 后，合回 `ops-stack` 或其它 monorepo 时应遵守的边界**，以减少联调断裂与重复劳动。

权威细节仍以 **`docs/ENGINEERING.md`**、**`docs/OPERATIONS.md`**、**`docs/CHANGELOG.md`** 为准；冲突时以代码与上述文档为准。

---

## 1. 定位与依赖边界

1. **`agent-os-runtime` 是运行时终点（③）**：可单独拷贝本目录研发；**禁止**在 `src/agent_os/` 内新增对 **`ops-knowledge`、`ops-distiller-forge`、`video_raw_ingest`** 等 Python 包的 `import` 或进程内耦合。与上游的衔接只能通过 **环境变量**、**JSON 文件路径**、**HTTP/API**。
2. **包与 CLI 标识保持稳定**（合回 monorepo 时强烈建议不改）：
   - `pyproject.toml` 中 `name = "agent-os-runtime"`；
   - 包目录名 **`agent_os`**（import 根）；
   - 控制台入口 **`agent-os-runtime`** / **`python -m agent_os`**。  
   若必须改名，须同步修改 **`ops-stack`** 内所有文档、`pipeline-demo`、以及用户脚本，并视为 **破坏性变更**。

---

## 2. 对外契约（合回前尽量不要破坏）

以下被 **`ops-knowledge` / `ops-distiller-forge` / `pipeline-demo` / 运维脚本** 间接依赖，变更前需评估并写 **`CHANGELOG.md`**：

| 类别 | 约束 |
|------|------|
| **环境变量名** | 使用 `AGENT_OS_*`、`OPENAI_*`、`NEO4J_*`、`MEM0_*` 等语义清晰的前缀；不要重新引入旧项目名前缀。 |
| **`AGENT_OS_MANIFEST_DIR`** | 目录内 **`*.json`** 文件名即 **`skill_id`**；与 **`ops-distiller-forge`** 的 `export-manifest` 产出字段对齐（可拷贝为 **`default_agent.json`**）。扩展字段可增，**勿随意改必填语义或删字段**。 |
| **`AGENT_OS_DEFAULT_SKILL_ID`** | 须为注册表中存在的 skill；与 **`get_agent(..., skill_id=None)`** 解析一致。 |
| **`AGENT_OS_HANDOFF_MANIFEST_PATH`** | `handbook_handoff.json` 中 **`video_raw_ingest_schema_ref`** 等字段仅作文本/展示用；勿假设本包会加载 ① 的代码。 |
| **`client_id` / `user_id`** | 所有记忆与检索工具必须继续支持租户隔离（见 **ENGINEERING.md §6**）。 |
| **工厂函数** | 外部集成优先依赖 **`agent_os.agent.factory.get_agent` / `get_reasoning_agent`** 及 **`Settings.from_env()`**；签名变更视为公共 API 变更。 |
| **`get_agent(..., exclude_tool_names=...)`** | Web 演示等调用方依赖；删除或改名参数须同步更新 **`examples/web_chat_fastapi.py`** 与相关测试。 |
| **`get_agent(..., skill_id=...)`** | 与 Graphiti **`graphiti_group_id`**、manifest 注册表绑定；删除或改名须同步 CLI / Web / 文档。 |

---

## 3. 架构红线（与设计决策一致）

1. **Graphiti / Neo4j**：运行时 **只读**（`search_`）；**禁止**在 Agent 运行路径中调用 **`add_episode`** 等写入 API。离线写入仅保留在 **`graphiti-ingest` CLI** 等明确离线入口（见 **ENGINEERING.md**）。
2. **记忆写入**：推荐经 **`MemoryController`** 与既有工具语义；不要绕过租户键向 Mem0/Hindsight 乱写。
3. **资源路径**：包内资源使用相对 **`agent_os` 包** 的路径（如 `Path(__file__)`），**不要**写死依赖 **`ops-stack` 仓库根** 的相对路径（`examples/` 下脚本以自身目录定位 `.env` 为例外，见 `web_chat_fastapi.py`）。

---

## 4. Skill 与可选能力

- **`skill_id`**：业务主键（如 **`default_agent`** 或外部 skill id），由 **`AGENT_OS_MANIFEST_DIR`** 下 JSON 文件名与包内置配方共同定义；新增 skill 须在 **ENGINEERING / OPERATIONS** 中说明，并考虑 **Graphiti `group_id`** 与 **增量工具** 兼容性。
- **可选 extras**：`[graphiti]`、`[mcp]`、`[web]`；新增 extra 时更新 **`README.md`** 与 **`pyproject.toml`**，避免默认安装变重。

---

## 5. 合回 monorepo 前自检清单

在将修改后的 **`agent-os-runtime/`** 目录替换回 **`ops-stack/agent-os-runtime/`** 之前建议完成：

1. **`pytest`**（在 `agent-os-runtime` 根，已 `pip install -e ".[dev]"`）。
2. **`agent-os-runtime doctor`**（可选 **`--strict`**，视 CI 要求）。
3. 若改了用户可见行为或契约：**`docs/CHANGELOG.md`** 已更新，**`pyproject.toml` 的 `version`** 与 **`agent_os.__version__`**（若存在）一致策略。
4. 若改了环境变量或安装步骤：**`docs/OPERATIONS.md`**（及必要时 **ENGINEERING.md**）已更新。
5. 未将 **`.env`**、**`.venv`**、**`data/*.json`** 等本地机密或缓存提交进 Git。

---

## 6. 文档与链接

- **独立仓库**中 `README.md` 里的 **`../PIPELINE.md`** 可能失效：可改为指向 monorepo 的 **绝对 URL**，或注明「仅 monorepo 内有效」。
- 架构与边界以 **`docs/ENGINEERING.md`** 为准；操作与变量表以 **`docs/OPERATIONS.md`** 为准。

---

## 7. 与 `coding-sync`（若仍在 monorepo 内协作）

在 **`ops-stack`** 内开发时，换机/收工可按仓库根 **`coding-sync/README.md`** 追加会话记录；**仅改 `agent-os-runtime` 时**仍应遵守本文件约束，便于他人合回同一目录树。
