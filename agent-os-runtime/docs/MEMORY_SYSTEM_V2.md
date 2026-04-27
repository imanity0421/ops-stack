# Memory System V2 设计记录

**读者导航**：先读仓库 [README.md](../README.md) §**文档与阅读顺序** 与 [ENGINEERING.md](ENGINEERING.md) §3。本文是记忆子系统的**设计说明**（已删去与 V2.2/CHANGELOG 重复的「待办清单」大段）。**Hindsight 以 V2.2 P1 为准**：append-only、**`supersedes` = 召回降权**、**`event_at` / `recorded_at` 分计**；实现见 `hindsight_retrieval.py` / `HindsightStore`。

---

本文档记录 agent-os-runtime 记忆系统的工程设计演进。目标是把
`MemoryController` 从简单的写入分流器升级为统一的记忆治理层。

## 当前阶段边界

当前阶段的主线是 **Memory V2 核心闭环**，不是权限平台、配置中心或分布式治理系统。

本阶段只要求完成：

1. `MemoryController` 能统一编排 Mem0、Hindsight、Graphiti、Asset Store 的检索上下文。
2. 四类记忆各自有清晰职责、基础 schema、写入/召回规则和迁移兼容策略。
3. Agent 在常规单实例或轻量多实例部署下，能稳定使用这些记忆，不把脏数据、不合适的数据写入错误层。

Graphiti 权限持久化、审计、Web 管理接口、并发写保护等属于**支撑能力**。当前阶段保留轻量文件权限模型；SQLite/Postgres 权限后端已回退，避免把 Memory V2 的主线扩展成权限平台或分布式治理系统。

## 总体分工

- **Mem0 / local memory**：当前状态机。保存公司级和个人级当前有效事实、稳定偏好、品牌约束。
- **Hindsight**：历史经验池。保存任务反馈、教训、复盘、结果信号；append-only，通过召回排序治理。
- **Asset Store**：素材与案例资产池。保存风格范例、背景素材、系统金牌案例、客户共享案例、个人案例。
- **Graphiti**：系统级干净知识图谱。只保存平台审核后的 SOP、方法论、行业知识与合规规则；`client_id` 只用于权限过滤，不表示客户自有图谱。

## Scope 约定

统一使用显式 scope，而不是让 `client_id` / `user_id` 隐式承载全部语义。

- `system`：系统级资产或知识，普通客户不可写。
- `client_shared`：客户/机构共享数据。
- `user_private`：某个用户私有数据。
- `task_scoped`：某个任务内的反馈或经验。

Mem0 中公司共享状态使用保留用户：

```text
__client_shared__
```

新写入不再使用 `user_id=None` 表示公司共享状态。旧数据可兼容读取，但不作为新写入目标。

## Mem0

Mem0 保存当前应该相信的状态。

推荐 metadata：

```json
{
  "memory_version": "2.0",
  "scope": "client_shared",
  "client_id": "ABC",
  "user_id": "__client_shared__",
  "skill_id": null,
  "fact_type": "brand_rule",
  "authority": "user_declared",
  "source": "agent_tool",
  "confidence": 0.9,
  "recorded_at": "2026-04-26T00:00:00Z",
  "effective_at": null,
  "expires_at": null,
  "status": "active"
}
```

召回策略：

1. 有 `user_id` 时同时读取 `client_shared` 与 `user_private`。
2. 无 `user_id` 时读取 `client_shared`，兼容读取旧的 legacy bucket。
3. 冲突由确定性 metadata 与系统宪法共同处理：公司事实、品牌规则、合规约束优先于个人偏好；个人表达风格在不触犯公司规则时优先。

## Hindsight

Hindsight 保存历史经验，不保存当前真相。它应支持经验召回排序，而不是全量注入模型。

推荐记录：

```json
{
  "memory_version": "2.0",
  "event_id": "hst_...",
  "type": "lesson",
  "client_id": "ABC",
  "user_id": "USER_123",
  "task_id": "TASK_001",
  "skill_id": "short_video",
  "deliverable_type": "script",
  "text": "同类脚本开头不要铺垫，要先抛冲突。",
  "source": "async_review",
  "confidence": 0.75,
  "outcome": "failure",
  "outcome_score": 0.3,
  "is_success": false,
  "conversion_rate": null,
  "tags": ["开头弱", "冲突不足"],
  "evidence_refs": ["feedback_..."],
  "recorded_at": "2026-04-26T00:00:00Z"
}
```

召回流程：

1. `client_id` 硬过滤，跨 client 默认禁止。
2. **取代关系（supersedes，V2.2 冻结）**：若行 B 含 `supersedes_event_id` 指向行 A 的 `event_id`，则 **A、B 均仍保留在 JSONL**；召回排序时对 **A 施加降权**（`HindsightRetrievalPolicy.superseded_penalty`），**不**以「删行」或「从候选集硬删除」为默认语义；新版仍可通过高相关度出现在后续诊断视图中。链式取代时，被链条中任一新行 supersedes 的 `event_id` 在评分阶段按 **superseded** 计。
3. **同类合并与频次**：对 `type` + 规范化正文（折叠空白、casefold）相同的行合并为一条展示；合并后对分数给予有界对数加成；行内可选 `weight_count`（默认 1）与合并行数一起展示为「同类×n，总权重×w」。可用 **`AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE=0`** 关闭合并/频次加分（**仍**对 supersedes 行计降权）。
4. 用 query 相似度、同 user、同 task、同 skill、同 deliverable、时效性、置信度、结果信号进行候选排序（在合并桶上取代表行打分）。
5. 阈值剪枝并保留 top-N 候选。
6. 可选 LLM 对候选池做相关性判断、语义去重、冲突合并和摘要。
7. 最终只注入少量“本轮可用教训”。

写入侧可选字段（JSONL）：

- `supersedes_event_id`：字符串，指向本租户内已存在的 `event_id`。
- `weight_count`：整数 1–10000，表示本条在统计上的权重（例如多次观测合并写入）。

### supersedes 自动建议（可选）

`AsyncReview` 可对「新 lesson + 小候选池（同 `client_id`，优先同 skill/task）」产出 **是否建议** 指向某条旧 `event_id` 的结构化结果；**默认不**自动写 `supersedes_event_id`（建议开关名 `AGENT_OS_HINDSIGHT_AUTO_SUPERSEDES=0`），避免误链。细粒度判断标准与安全边界以代码与测试为准。

**Hindsight 候选池 LLM 再加工**（与上不同，是「召回后润色/去重」）默认关：

```text
AGENT_OS_ENABLE_HINDSIGHT_SYNTHESIS=1
AGENT_OS_HINDSIGHT_SYNTHESIS_MODEL=gpt-4o-mini
AGENT_OS_HINDSIGHT_SYNTHESIS_MAX_CANDIDATES=20
```

开启后仍先走确定性过滤与 top-N，再对**候选池**加工，不扫全量 JSONL。

## Asset Store

Asset Store 不再把 `skill_id` 当硬分区。`skill` 仅作为弱标签和排序加权因子。

核心资产类型：

- `style_reference`：风格范例、金牌案例、爆款脚本。注入为 `[Few-Shot Examples]`，只参考语气、结构和节奏。
- `source_material`：背景素材、采访、故事、产品资料。注入为 `[Background Context]`，只提取事实和细节。

推荐记录：

```json
{
  "asset_id": "ast_...",
  "memory_version": "2.0",
  "scope": "system",
  "client_id": "system_global",
  "owner_user_id": null,
  "asset_type": "style_reference",
  "raw_content": "...",
  "feature_summary": "痛点前置、强转折的短视频脚本范例。",
  "primary_skill_hint": "short_video",
  "applicable_skill_ids": ["short_video", "xiaohongshu_post"],
  "skill_confidence": 0.82,
  "style_tags": ["痛点前置", "悬念开场", "神转折"],
  "content_tags": ["宝妈减脂"],
  "quality_score": 0.9,
  "risk_flags": [],
  "source": "manual_import",
  "created_at": "2026-04-26T00:00:00Z"
}
```

权限与 fallback：

1. `user_private`：仅当前用户可查。
2. `client_shared`：同 client 可查。
3. `system`：系统金牌案例，作为冷启动 fallback。

Asset Store 也支持可选 LLM 加工层：

```text
AGENT_OS_ENABLE_ASSET_SYNTHESIS=1
AGENT_OS_ASSET_SYNTHESIS_MODEL=gpt-4o-mini
AGENT_OS_ASSET_SYNTHESIS_MAX_CANDIDATES=12
```

开启后，系统仍先执行权限过滤、scope fallback、asset_type/skill hint 排序和 top-N 剪枝；LLM 只处理候选池。输出会按用途拆成：

- `[Few-Shot Guidance]`：来自 `style_reference`，只用于语气、结构、节奏参考。
- `[Background Context]`：来自 `source_material`，只用于事实、故事和细节提取。

实现上允许保留 `client_id="system_global"` 作为系统资产哨兵值，但必须同时写入 `scope="system"`，避免长期依赖 client_id 承载 scope。

## Graphiti

Graphiti 只保存系统级干净知识。客户专属事实、素材和经验应进入 Mem0、Asset Store 或 Hindsight。

未来方向：

- Graphiti group 以 `skill_id` / `domain` 为主。
- `client_id` 只用于权限过滤：`client_id -> allowed_domains / allowed_skill_ids / allowed_entitlements`。
- 客户实践中出现的全局通用经验，需由人工审核、提炼、版本化后写入 Graphiti。

迁移兼容：

- 新写入使用系统级 group：`system_graphiti_group_id(skill_id)`。
- 只读检索会优先查系统级 group。
- 若系统级 group 没有结果，默认兼容读取旧的 `graphiti_group_id(client_id, skill_id)` 分区，避免历史 JSONL/Neo4j 数据突然不可用。
- 可通过 `AGENT_OS_GRAPHITI_ENABLE_LEGACY_CLIENT_GROUPS=0` 关闭旧分区只读兜底。

## 迭代顺序

1. 引入统一 scope 与 metadata schema。
2. Mem0 改为 `__client_shared__` + `user_private` 双路召回。
3. Hindsight 扩展 schema 并增加加权召回。
4. Asset Store 引入 `asset_type`、`scope`、`applicable_skill_ids`，弱化 `skill_id` 硬过滤。
5. Asset Store 支持 system fallback。
6. Graphiti 改为系统知识分区语义，并预留 client 权限过滤。
7. 引入可选 LLM rerank / dedup / summarize。
8. 收敛文档和运维边界，停止围绕单个子模块继续扩大“生产级”治理范围。

## 运维与数据迁移

- **编排入口**：`retrieve_ordered_context` 的 Mem0→Hindsight→Graphiti→Asset 组装逻辑由 `MemoryController.retrieve_ordered_context` + `agent_os.memory.ordered_context` 统一实现；工具层仅绑定租户与开关。运维侧行为以本文档与 **`docs/OPERATIONS.md`** 的「Memory V2 运维」为准。
- **本地 Mem0 桶归一**：将旧版 `users[<client_id>]`（无 `::`）合并到 `users[<client_id>::__client_shared__]` 时，使用：

  ```bash
  python scripts/migrate_memory_v2.py local-memory --path data/local_memory.json --dry-run
  python scripts/migrate_memory_v2.py local-memory --path data/local_memory.json
  ```

- **Graphiti JSONL fallback**：将旧 `client__skill` 分区键追加/替换为系统级 `skill` 分区时：

  ```bash
  python scripts/migrate_memory_v2.py knowledge-jsonl --path data/knowledge.jsonl --mode duplicate --dry-run
  ```

  详见 `src/agent_os/memory/migration_v2.py` 与 **`docs/OPERATIONS.md`**。

## 最小验收清单（当前阶段）

当前阶段完成的是 **Memory V2 核心闭环**。满足以下条件即可认为本阶段完成；未列入的分布式治理、权限平台和管理后台增强，不作为本阶段验收项。

### 必须满足

- **统一编排入口**：业务侧优先调用 `MemoryController.retrieve_ordered_context` 或 Agent 工具 `retrieve_ordered_context`，四层上下文按 Mem0 → Hindsight → Graphiti → Asset Store 固定顺序输出。
- **Mem0 / local memory**：公司共享事实写入 `client_shared`（本地桶为 `__client_shared__`），个人偏好写入 `user_private`；检索时合并公司共享与个人私有，并兼容旧本地桶。
- **Hindsight**：任务反馈写入 append-only JSONL；检索时按 `client_id` 硬隔离，支持 `supersedes_event_id` 对旧行在排序中**降权**，支持同类合并与 `weight_count` 频次权重。
- **Graphiti**：只作为系统级干净知识层；新语义优先读取 `system_graphiti_group_id(skill_id)`，必要时只读兼容 legacy `graphiti_group_id(client_id, skill_id)`，且可通过环境变量关闭 legacy 兜底。
- **Asset Store**：资产记录带 `scope`、`asset_type`、`primary_skill_hint` / `applicable_skill_ids`；检索遵守 `user_private`、`client_shared`、`system` 可见性，并允许 system 资产作为冷启动 fallback。
- **迁移兼容**：本地 Mem0 旧桶归一与 Graphiti JSONL fallback 分区迁移有脚本和 dry-run 路径。
- **测试兜底**：至少覆盖四层完整召回、工具入口、Graphiti legacy 开关、Hindsight 租户隔离、Asset scope 可见性、Mem0 V2 metadata 落盘。

### 明确不要求

- 不要求 Graphiti 权限进入数据库、配置中心或跨实例一致性系统。
- 不要求审计日志、幂等键、Web 管理接口继续增强。
- 不要求 Hindsight 自动判断取代链；当前只要求字段与检索语义可用。
- 不要求 Asset Store 的 LLM 加工层默认开启；LLM synthesis 仍是成本开关。

### 建议验收命令

Memory V2 定向验收：

```bash
ruff check src tests
PYTHONPATH=src:. pytest tests/core/test_memory_controller.py tests/core/test_ordered_context.py tests/core/test_hindsight_merge.py tests/core/test_migration_v2.py tests/core/test_knowledge.py tests/core/test_asset_ingest.py tests/core/test_asset_store_format.py tests/core/test_build_memory_tools_flags.py tests/core/test_ingest_gateway.py
```

合入前全仓回归（覆盖面更大，不替代上面的定向说明）：

```bash
PYTHONPATH=src:. pytest
```

## Memory V2.1 在线治理增强（已合入，本节只记要点）

目标：写路径统一经 **`MemoryController` → `MemoryPolicy` → `MemoryLedger`（持久化去重/审计）**；`AsyncReview` 不直写 `HindsightStore` 低层。Ledger **表结构**在代码 `agent_os.memory.ledger`；canonical：`trim` + 折叠空白 + `casefold`。**`TaskMemory` + `TaskSummaryService`** 做**当前 session 内**滚动 summary（不沉淀到 Mem0/Hindsight；触发阈值见 `AGENT_OS_TASK_*` 与 [OPERATIONS.md](OPERATIONS.md)）。**Hindsight** 需 **`event_at`（可空）+ `recorded_at`（事务时间）**；检索渲染见 temporal grounding 配置。

**验收**：已满足「重启不重复写」「账本状态可追踪」「task summary 不跨 session 冒充长期事实」等（细节以测试与 CHANGELOG 为准）。

## Memory V2.2 执行清单：Append-only 经验治理

V2 与 V2.1 的核心闭环已经完成。V2.2 不改变四层架构，也不把 Asset Store 纳入高频在线写入 Controller；主线是修正已发现的实现缺陷，并把 Hindsight 从“能写能查”升级为 append-only 经验日志 + 评分型召回治理。

### 0. 边界确认

- **Policy 防火墙优化**：属于独立非耦合事项，可以并行列入工作计划；它只决定候选记忆是否允许写入，不承担 Hindsight 召回演化。
- **Hindsight**：继续坚持 append-only，只增不减。历史行不因取代、总结或压缩而删除；防遗忘与记忆爆炸治理通过时序、评分、聚类、预算和派生 summary/index 在召回层完成。
- **Asset Store**：定位为离线长文资产治理域，主要保存几千字级案例、素材和风格参考。它不走高频 `MemoryController` 写入路径，而由离线导入、清洗、特征抽取、去重、人工复核和 scope 可见性治理。
- **Graphiti**：仍作为系统级静态知识图谱，不接受普通用户在线写入；双时态不强制覆盖此层。

### P0：实现缺陷修复

1. **FastAPI 复盘传参错误**
   - 修复 `examples/web_chat_fastapi.py` 中 `AsyncReviewService.from_env(ctrl.hindsight_store)`，应传入 `MemoryController`。
   - 增加或补充验证，避免业务入口把低层 store 当成 controller 传入。

2. **Dedup 指纹统一**
   - `MemoryController._fingerprint` 必须复用 `MemoryLedger` 的 canonical 规范化规则（trim、折叠空白、casefold）。
   - 目标：进程内去重与持久化 ledger 去重对同一文本给出一致判断，避免重启前后或空白差异导致行为分裂。

3. **Hindsight 双时态排序修正**
   - `event_at` 用于判断物理现实中的新旧；`recorded_at` 用于判断系统记录的新旧。
   - 排序时不得继续使用 `event_at or recorded_at` 混合字段。
   - 缺少 `event_at` 的 legacy 行继续可读，但在现实新鲜度上不冒充新事件；可由 `recorded_at` 提供轻量系统记录新鲜度。

4. **审计语义**：`ingest_user_fact` 已接入 `idempotency_key` 与 `memory_write_attempts`（duplicate/reject 可追踪）；`HindsightStore.append_lesson` 仍**仅**低层/测试/迁移。

### P1：极简验收范围（已完成）

P1 只保留 Hindsight append-only 经验治理的最小闭环，不承接全部新增发现：

1. **召回策略模块化**：抽出 `HindsightRetrievalPolicy`，把 Hindsight 召回从存储读写中拆出来，便于测试与解释。
2. **Append-only 语义修正**：`supersedes_event_id` 不删除、不隐藏旧行，只作为旧行召回降权信号。
3. **双时态排序修正**：`event_at` 表示现实事件时间，`recorded_at` 表示系统记录时间，评分时不再混用。
4. **基础 query/预算/解释**：补轻量中文 query 特征、基础 cluster/category 预算与 `debug_scores` 解释输出，避免明显过度召回。
5. **经验质量字段入链**：Hindsight 新写入和召回评分支持 `validity_score`、`specificity_score`、`recurrence_count`、`negative_evidence_count`、`last_reinforced_at`。
6. **AsyncReview 结构化候选**：复盘输出优先使用结构化 JSON lesson，并兼容旧文本格式；写入仍经过 MemoryPolicy 与 MemoryController。

P1 结论：**已完成并冻结**。后续发现不再自动并入 P1，按真实优先级进入 P2/P3。

### P2：继续执行清单（**已全部 close**；表内为速查）

| 项 | 要点 |
|----|------|
| Policy | 规则 gate 分类/secret 拦截；`AGENT_OS_MEMORY_POLICY_MODE=warn/audit`；`evaluate_policy_cases`；warn 审计需 ledger 路径。 |
| Ledger 幂等 | `source_message_id` → `idempotency_key`；duplicate / reject / `memory_write_attempts` 见代码。 |
| Hindsight 调试 | `debug_scores` 与 Web `/api/.../search` 受控；`AGENT_OS_ENABLE_HINDSIGHT_DEBUG_TOOLS` |
| Hindsight 索引 | JSONL sidecar、CLI `hindsight-index`；与 JSONL 行删/向量化同步；细节见 [OPERATIONS.md](OPERATIONS.md) |
| outcome / 强化 | 上游可传**真实** outcome 覆盖弱模型信号；`recurrence_count` / `negative_evidence` / `last_reinforced_at` 文本近邻式推导。 |
| Task 防断线 | `TaskMemory` + 滚动 summary，**不得**当长期事实写入四层长期存储。 |

### P3、P4 与「未做」

**P3-1（Hindsight 向量混合召回）— 已落地**：LanceDB sidecar、与确定性候选 **并集** 后同管线打分、`AGENT_OS_ENABLE_HINDSIGHT_VECTOR_RECALL` 等；`semantic_recall` 旧双轨已裁；失败回退纯 JSONL。更细的运维与风险见 [OPERATIONS.md](OPERATIONS.md) 与 CHANGELOG。

**P3-2/3 仍未执行**：真实业务/CI 的 outcome 评估闭环、长期派生 Hindsight **summary/index** 体系（不覆盖原 JSONL、不自动进 Mem0）。

**P4（只记不做）**：统一文件 watcher、全生命周期安全与「完整审计平台」等平台化工作。

*历史多轮 P0–P3 代码审查的逐条修复已并入 CHANGELOG/提交记录；此处不再双列，以免与上文重复。*

### 已写代码归属（结论不变）

- **保留**上述治理链相关实现；**不**为 P4 预写大量平台代码；评测飞轮、watcher、统一审计**仅**保留需求级记录，见上表「未做」与 [SPRINT_IMPLEMENTATION_ROADMAP.md](SPRINT_IMPLEMENTATION_ROADMAP.md)（若与实现冲突以代码为准）。

