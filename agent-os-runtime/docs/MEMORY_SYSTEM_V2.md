# Memory System V2 设计记录

本文档记录 agent-os-runtime 记忆系统的下一阶段工程设计。目标是把
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
2. **取代链（supersedes）**：若行含 `supersedes_event_id` 指向另一行的 `event_id`，则被指向行从召回中剔除（支持链式：仅保留未被任何行取代的事件）。
3. **同类合并与频次**：对 `type` + 规范化正文（折叠空白、casefold）相同的行合并为一条展示；合并后对分数给予有界对数加成；行内可选 `weight_count`（默认 1）与合并行数一起展示为「同类×n，总权重×w」。可用 **`AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE=0`** 关闭合并/频次加成（仍执行 supersedes 过滤）。
4. 用 query 相似度、同 user、同 task、同 skill、同 deliverable、时效性、置信度、结果信号进行候选排序（在合并桶上取代表行打分）。
5. 阈值剪枝并保留 top-N 候选。
6. 可选 LLM 对候选池做相关性判断、语义去重、冲突合并和摘要。
7. 最终只注入少量“本轮可用教训”。

写入侧可选字段（JSONL）：

- `supersedes_event_id`：字符串，指向本租户内已存在的 `event_id`。
- `weight_count`：整数 1–10000，表示本条在统计上的权重（例如多次观测合并写入）。

### supersedes 自动化策略（设计）

目标：减少人工维护 `supersedes_event_id`，但不让自动复盘误删或隐藏仍有价值的历史经验。当前阶段只做**建议策略设计**，不把自动取代作为默认写入行为。

#### 触发位置

优先放在 `AsyncReviewService` 写入 Hindsight 之前：

1. AsyncReview 先从对话中提炼 1-3 条候选 lesson。
2. 对每条候选 lesson，在同一 `client_id` 下检索少量历史 Hindsight 候选。
3. 生成 `supersedes_suggestion`，最多指向 1 条旧 `event_id`。
4. 默认只把建议写入审计/调试结果或返回给上层；只有在显式开启开关后，才把建议填入 `supersedes_event_id`。

#### 候选池约束

候选池必须小且同域，避免 LLM 在全量历史中做宽泛判断：

- 必须同 `client_id`。
- 优先同 `skill_id`；若候选 lesson 没有 `skill_id`，仅比较同 `task_id` 或文本高度相似的历史行。
- 优先同 `deliverable_type` / `task_id`。
- 只取未被其他行 supersede 的 active 事件。
- 候选数建议不超过 8 条，且只包含 `event_id`、`text`、`recorded_at`、`source`、`tags`、`outcome` 等必要字段。

#### 判断标准

只有满足以下条件时才建议 supersede：

1. 新 lesson 与旧 lesson 讨论的是同一个可执行规则或同一个错误模式。
2. 新 lesson 明确更具体、更准确，或纠正了旧 lesson。
3. 新 lesson 与旧 lesson 同时保留会造成冲突、重复注入或让模型犹豫。
4. 旧 lesson 不是通用背景知识，也不是仍适用于不同场景的经验。

不建议 supersede 的情况：

- 只是同主题但角度不同。
- 只是更近期，但没有更准确。
- 新 lesson 是一次性任务要求。
- 旧 lesson 仍适用于另一种 deliverable、skill 或客户上下文。

#### 建议输出结构

后续实现时，LLM 或确定性策略应输出结构化结果，而不是自由文本：

```json
{
  "lesson_text": "新版教训正文",
  "supersedes_event_id": "hst_...",
  "confidence": 0.0,
  "reason": "新版规则更具体，旧规则会导致开头继续铺垫",
  "decision": "suggest_supersede"
}
```

`decision` 仅允许：

- `suggest_supersede`：建议填入 `supersedes_event_id`。
- `keep_both`：两条都保留。
- `no_match`：候选池中没有应取代的旧事件。

#### 安全边界

- 默认不开启自动取代；开关建议命名为 `AGENT_OS_HINDSIGHT_AUTO_SUPERSEDES=0`。
- 即使开启，也只允许取代同租户 active 事件，禁止跨 `client_id`。
- 一次新 lesson 最多取代 1 条旧事件。
- 低置信度（建议阈值 `< 0.75`）只记录建议，不写入 `supersedes_event_id`。
- 所有自动建议应记录 `source="async_review"` 与 reason，便于回溯。

#### 最小实现路径（后续）

1. 在 `HindsightStore` 增加一个只读候选查询方法，例如 `find_supersede_candidates(...)`，返回同租户 active 候选行。
2. 在 `AsyncReviewService` 内新增可选策略函数，对新 lesson + 候选池输出结构化建议。
3. 默认仅测试确定性候选过滤与结构化解析；LLM 判断用 mock 覆盖，不把真实 LLM 放进单测。
4. 开关关闭时保持现有行为完全不变。

LLM 加工层默认关闭，可通过环境变量开启：

```text
AGENT_OS_ENABLE_HINDSIGHT_SYNTHESIS=1
AGENT_OS_HINDSIGHT_SYNTHESIS_MODEL=gpt-4o-mini
AGENT_OS_HINDSIGHT_SYNTHESIS_MAX_CANDIDATES=20
```

开启后，系统仍先执行确定性的租户过滤、metadata 加权和 top-N 剪枝；LLM 只处理候选池，不直接访问全量 Hindsight。

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
- **Hindsight**：任务反馈写入 append-only JSONL；检索时按 `client_id` 硬隔离，支持 `supersedes_event_id` 隐藏旧事件，支持同类合并与 `weight_count` 频次权重。
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

## 后续优化待办（收敛版）

以下清单只跟踪 Memory V2 主线。Graphiti 权限管理已经具备基础可用能力，后续除非明确进入平台化阶段，否则不再继续扩展。

### 核心保留

- **已完成**：`retrieve_ordered_context` 收敛到 `MemoryController`，按 Mem0 → Hindsight → Graphiti → Asset Store 的顺序组装上下文。
- **已完成**：Mem0 使用 `__client_shared__` 与 `user_private` 双路召回，并兼容旧本地桶。
- **已完成**：Hindsight 支持 `supersedes_event_id`、`weight_count`、同类合并与频次加权召回。
- **已完成**：Asset Store 引入 `asset_type`、`scope`、`applicable_skill_ids`，支持 system fallback。
- **已完成**：Graphiti 改为系统知识分区，保留 legacy client-skill 分区只读兼容。
- **已完成**：基础迁移脚本覆盖本地 Mem0 桶归一与 Graphiti JSONL fallback 分区迁移。

### 可选支撑能力

- **已完成（可选）**：Graphiti 权限文件、CLI 管理命令、`doctor` 结构检查。
- **已完成（可选，默认关闭）**：Web 内网管理接口、token 鉴权、审计日志、热加载、revision 冲突检测。
- **已回退**：SQLite/Postgres 权限后端能力。当前阶段只保留文件权限模型，避免继续牵引 Memory V2 平台化。

### 真正值得继续做

- **已完成（设计）**：Hindsight supersedes 自动化策略，由 review 结果建议可能被取代的 `event_id`，减少人工维护链；默认只建议，不自动取代。
- **已完成**：围绕 `retrieve_ordered_context` 增加端到端质量样例，验证四层记忆在典型任务中的注入顺序、去重与冲突处理。
- **已完成**：补充 Memory V2 的最小验收清单，明确哪些行为属于当前阶段完成，哪些属于后续平台化工程。

## Memory V2.1 在线治理增强

Memory V2 核心闭环已经完成。V2.1 不扩展 Graphiti 权限平台、MCP 世界感知或离线炼金能力，而是补齐在线记忆底座的四个治理缺口：

1. `AsyncReviewService` 不能绕过 `MemoryController` 与 `MemoryPolicy`。
2. Dedup 不能只存在于进程内存；重启后同一条记忆不得重复写入。
3. `TaskMemory` 不能只保留 schema；需要形成自动滚动 summary 闭环。
4. `Hindsight` 需要补齐双时态，区分真实事件时间与系统记录时间。

### 1. 统一写入入口

所有在线写入必须统一进入：

```text
业务/工具/AsyncReview/Ingest
  -> MemoryController.ingest_user_fact(UserFact)
  -> MemoryPolicy
  -> MemoryLedger / Dedup
  -> Mem0 或 Hindsight
```

`HindsightStore` 是低层 append-only 存储适配器，不再作为业务层推荐写入口。`AsyncReviewService` 只能生成候选 `UserFact`，再交给 `MemoryController` 写入；这样 policy、dedup、scope、metadata、双时态和审计语义都不会被绕开。

### 2. 持久化 Dedup Ledger

V2.1 引入轻量 SQLite `MemoryLedger`，用于持久化写入账本和幂等去重。目标不是做权限平台，而是解决单机/轻量部署下的重复写入、审计与失败恢复。

推荐字段：

```sql
memory_write_ledger (
  ledger_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  user_id TEXT,
  scope TEXT NOT NULL,
  lane TEXT NOT NULL,
  target TEXT NOT NULL,
  canonical_hash TEXT NOT NULL,
  idempotency_key TEXT,
  text_norm TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  policy_reason TEXT,
  storage_ref TEXT,
  recorded_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(client_id, user_id, scope, lane, canonical_hash)
)
```

写入状态建议只保留少量可解释状态：

- `pending`：已登记，底层存储尚未提交。
- `committed`：底层存储写入成功；后续相同 canonical hash 直接跳过。
- `duplicate`：本次请求命中已提交记录。
- `rejected`：被 policy 拒绝。
- `failed`：底层写入失败，可按状态和时间决定是否重试。

最小 canonical 规则：trim、折叠空白、casefold。不要过度清洗中文标点，避免把不同规则误合并。

### 3. TaskMemory 自动滚动 Summary

`TaskMemoryStore` 继续限定为同 session 的 working memory，不承载跨 session 长期事实。V2.1 增加 `TaskSummaryService`，在当前 task 的未总结消息超过阈值时自动生成或合并 summary，并写回 `task_summaries`。

触发条件：

- 当前 task 消息数达到 `AGENT_OS_TASK_SUMMARY_MIN_MESSAGES`。
- 距离上次 summary 新增消息数达到 `AGENT_OS_TASK_SUMMARY_EVERY_N_MESSAGES`。
- CLI/API 当前轮 assistant 输出后触发一次轻量更新。

Summary 使用结构化格式，目标是恢复工作现场，而不是复述聊天：

```text
- 当前任务目标：
- 用户已确认的约束：
- 已做出的关键决定：
- 当前交付物状态：
- 待办/未决问题：
- 不要重复尝试的方向：
- 最近一次用户反馈：
```

注入顺序建议：

```text
当前 task summary
  -> 本 session 任务短索引
  -> Agno 最近 N 条消息
  -> retrieve_ordered_context
```

Summary 指令必须继续声明：只用于当前 session/task 连贯性，不代表长期事实，不得自动写入 Mem0/Hindsight/Asset/Graphiti。

### 4. Hindsight 双时态

Hindsight 至少补齐 `event_at` 与既有 `recorded_at`：

- `event_at`：经验对应的真实事件发生时间，可为空。
- `recorded_at`：系统写入该记忆的事务时间，必须存在。

后续可继续扩展 `observed_at`、`effective_at`、`expires_at`，但 V2.1 不强制所有旧数据迁移。legacy JSONL 缺少 `event_at` 时应继续可读，并在 temporal grounding 中显示“发生时间未知”。

推荐渲染：

```text
[发生于 2026-04-25T15:20:00Z | 记录于 2026-04-26T09:03:12Z | 来源 async_review] ...
```

排序时，相关性仍优先；`event_at` 用于判断现实新旧，`recorded_at` 用于判断系统记录新旧。两者冲突时不隐藏信息，由 temporal grounding 暴露给模型。

### V2.1 最小验收清单

- `AsyncReviewService` 不再直接调用 `HindsightStore.append_lesson`。
- 同一条 Mem0/Hindsight 记忆在进程重启后不会重复写入。
- 写入账本能追踪 `pending`、`committed`、`duplicate`、`rejected`、`failed`。
- Hindsight 新写入同时具备 `event_at` 语义与 `recorded_at` 事务时间。
- 长对话超过阈值后能自动生成或更新当前 task summary。
- 下一轮 agent 构建时会注入最新当前 task summary。
- 旧 JSONL、本地 Mem0 和旧 task summary 数据继续可读，缺字段不崩溃。

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

4. **审计语义补强（后续 P0/P1 交界）**
   - `MemoryLedger.idempotency_key` 已有字段，后续应接入 `MemoryController.ingest_user_fact`。
   - ledger 命中 duplicate 时，API 语义和持久审计语义需保持一致；是否新增 duplicate attempt 行可单独评估。
   - 业务层不推荐直接调用 `HindsightStore.append_lesson`，它保留为低层/测试/迁移适配器。

### P1：极简验收范围（已完成）

P1 只保留 Hindsight append-only 经验治理的最小闭环，不承接全部新增发现：

1. **召回策略模块化**：抽出 `HindsightRetrievalPolicy`，把 Hindsight 召回从存储读写中拆出来，便于测试与解释。
2. **Append-only 语义修正**：`supersedes_event_id` 不删除、不隐藏旧行，只作为旧行召回降权信号。
3. **双时态排序修正**：`event_at` 表示现实事件时间，`recorded_at` 表示系统记录时间，评分时不再混用。
4. **基础 query/预算/解释**：补轻量中文 query 特征、基础 cluster/category 预算与 `debug_scores` 解释输出，避免明显过度召回。
5. **经验质量字段入链**：Hindsight 新写入和召回评分支持 `validity_score`、`specificity_score`、`recurrence_count`、`negative_evidence_count`、`last_reinforced_at`。
6. **AsyncReview 结构化候选**：复盘输出优先使用结构化 JSON lesson，并兼容旧文本格式；写入仍经过 MemoryPolicy 与 MemoryController。

P1 结论：**已完成并冻结**。后续发现不再自动并入 P1，按真实优先级进入 P2/P3。

### P2：继续执行清单（治理、审计、运维）

P2 是“让现有 Memory V2 更可治理、更可调试、更可接入真实反馈”的工程层，不改变四层记忆架构。后续执行只允许从本节取任务；P3/P4 只记录，不实现。

1. **Policy 防火墙独立增强**：已完成。
   - 已完成：规则 gate 输出结构化分类、规则 id、严重度与命中信号，可区分敏感信息、临时/不确定内容、稳定偏好、长期事实、任务反馈、可执行 lesson、低信号内容。
   - 已完成：补充 secret-like 内容拦截，避免明显 API key / token / password 类文本进入记忆。
   - 已完成：`AGENT_OS_MEMORY_POLICY_MODE=warn|audit` 可作为 dry-run/audit 模式，策略拒绝项会放行但标记 `policy_warning`，并在配置 ledger 时把 `policy_warning:*` 写入 `policy_reason`。
   - 已完成：补充轻量固定评估集与 `evaluate_policy_cases()` report，用于观察规则 gate 的误杀/漏写；不做 LLM gate。
   - P2 风险：warn/audit 的持久审计依赖 `memory_ledger_path`；未配置 ledger 时只能日志告警，无法形成可查询的审计账本。

2. **MemoryLedger 审计语义补强**：已完成。
   - 已完成：`MemoryController.ingest_user_fact` 会把 `UserFact.source_message_id` 接入 `MemoryLedger.idempotency_key`。
   - 已完成：同一 client/user/scope/lane 下重复使用同一个 idempotency key，会按同一次上游写入处理；即使文本有细微变化，也会返回 `ledger_idempotency_*_duplicate`，避免重试导致重复记忆。
   - 已完成：Policy reject 写入账本时也会记录 `idempotency_key`，便于把被拒绝写入与上游消息关联。
   - 已完成：duplicate attempt 采用轻量 `memory_write_attempts` 行记录，仅记录幂等/哈希重复尝试的最小审计信息；不做完整审计平台。
   - P2 风险：当前幂等键来源是 `source_message_id`；若上游工具/API 不传该字段，仍只能退回 canonical hash 去重，无法表达“同一次请求但文本被重试改写”的幂等语义。

3. **Hindsight 调试入口治理**：已完成。
   - 已完成：Agent 工具 `search_past_lessons(..., debug_scores=True)` 与 `retrieve_ordered_context(..., debug_scores=True)` 可临时输出评分原因；默认关闭。
   - 已完成：Web 示例新增 `/api/memory/hindsight/search`，默认不返回评分，`debug_scores=true` 时必须通过 admin token/host 校验。
   - 已完成：Agent 工具侧新增显式调试运行开关，默认禁止 `debug_scores=True` 输出评分明细；仅 `AGENT_OS_ENABLE_HINDSIGHT_DEBUG_TOOLS=1` 或等价设置启用。

4. **Hindsight sidecar index 运维治理**：已完成。
   - 已完成：首次检索可构建 sidecar index；文件签名匹配时复用；append 时可增量更新。
   - 已完成：`HindsightStore.invalidate_index()` 可删除派生索引；Web 删除 Hindsight JSONL 行后会同步清理 `.index.json`。
   - 已完成：`HindsightStore` 提供 `index_status()` / `rebuild_index()` / `invalidate_index()`，CLI 提供 `agent-os-runtime hindsight-index status|rebuild|invalidate --path <hindsight.jsonl>`；不做 watcher。
   - P2 风险：若引入加密、脱敏或保留期策略，sidecar 必须与 JSONL 同步处理；该平台化安全治理归 P4。

5. **真实 outcome 接口约定**：已完成。
   - 已完成：`AsyncReviewService` 结构化候选可携带 `outcome` / `outcome_score` / `is_success`。
   - 已完成：调用方可通过 `submit` / `submit_and_wait` 传入真实 outcome，并优先覆盖模型候选值。
   - 已完成：接口约定明确为只有已经观测到真实验收结果的上游（如 Web session 结束、人工验收、外部调度器显式回填）可传入真实 outcome；模型复盘 outcome 只作为弱信号；已补最小测试。不接 CI/业务平台。
   - P2 风险：模型复盘产出的 outcome 只能作为弱信号，不能替代真实结果。

6. **经验强化生产者治理**：已完成。
   - 已完成：`AsyncReviewService` 写入前可用轻量历史候选匹配推导 `recurrence_count`、继承已有 `negative_evidence_count`，并设置 `last_reinforced_at` 为系统观测时间。
   - 已完成：字段语义固定为 `event_at` 表示真实反馈/经验事件发生时间，`recorded_at` 表示系统写入观察时间，`last_reinforced_at` 表示系统最近一次观测到相似强化证据的时间；已补最小测试。不接真实业务反馈源。
   - P2 风险：当前生产者仍是文本相似匹配，不能证明经验真实有效或无效；真实复用结果接入归 P3。

7. **当前 task 防遗忘治理**：已完成。
   - 已完成：当前 session/task 由 `TaskMemoryStore` 与 `TaskSummaryService` 负责。
   - 已完成：Task summary 指令明确仅用于当前 session/task 连贯性，不代表长期事实，不得自动写入 Mem0、Hindsight、Asset、Graphiti；已补最小验收测试。

P2 审核结论：**7 项均已完成，当前计划已 close**。P3/P4 后续必须独立启动、独立验收；发现的新问题必须按真实优先级归档。

### P3：语义质量增强

P3 是质量上限层，必须在不破坏 Hindsight append-only、不污染 Mem0/Asset/Graphiti 的前提下独立推进。

1. **Embedding / rerank 语义召回**：Hindsight Hybrid Recall 工程闭环已完成。
   - 已完成：正式路径为 Hindsight LanceDB 派生向量索引；原始 Hindsight 仍以 JSONL append-only 为准，向量索引只作为可重建 sidecar 候选池。
   - 已完成：启用 `AGENT_OS_ENABLE_HINDSIGHT_VECTOR_RECALL=1` 后，检索流程为 query embedding + metadata 过滤（client/user/task/skill/deliverable）取候选；向量候选会与确定性候选取并集，再回到现有 Hindsight scoring/budget/debug 管线排序。
   - 已完成：向量命中的 `_distance` 会以 `vector_distance` / `vector_bonus` 进入 debug reason 与最终排序，可用 `AGENT_OS_HINDSIGHT_VECTOR_SCORE_WEIGHT` 调整权重。
   - 已完成：向量 sidecar 行记录 `schema_version`、`embedding_model`、source size/mtime，`vector-status` 可判断索引是否 stale。
   - 已完成：增量 append 对同一 `source_path + event_id` 做逻辑 upsert，避免重放造成重复向量行。
   - 已完成：Web 手工删除 Hindsight 行会同步清理 JSON sidecar 与 vector sidecar，避免幽灵向量。
   - 已完成：Hindsight JSONL append 增加跨进程文件锁；Ledger SQLite 增加 busy timeout / WAL / NORMAL synchronous，降低并发写风险。
   - 已完成：CLI 通过 `agent-os-runtime hindsight-index vector-status|vector-rebuild|vector-invalidate` 维护 LanceDB sidecar。
   - 已裁剪：本地轻量 `semantic_recall` fallback 已移除，避免与 LanceDB Hybrid Recall 形成双轨语义排序。
   - 边界：Mem0 不接 LanceDB sidecar，避免与 Mem0 自身后端搜索形成两套长期画像索引；Asset Store 继续使用既有 LanceDB 能力。
   - 后续平台化空间：真正原子 rebuild、批量 embedding/限流/成本报表、LanceDB where 下推优化可归入 P4 运维平台化；当前失败时回退 JSONL 确定性检索，不影响原始记忆可用性。

### 本轮代码审查记录（P0-P3-1）

- 已修复：P3-1 向量候选与确定性候选原为二选一，已改为并集后统一 rerank，避免向量索引缺行导致新 JSONL 经验漏召回。
- 已修复：Web 删除 Hindsight 行后只清理 JSON sidecar，已补 vector sidecar invalidation。
- 已修复：Hindsight JSONL append 无跨进程锁，已补轻量文件锁。
- 已修复：Ledger SQLite 默认连接策略偏弱，已补 busy timeout / WAL / NORMAL synchronous。
- 已修复：vector rebuild 删除旧同源行失败后继续 add 的重复风险，已改为返回 error。
- 已裁剪：本地 semantic fallback 属于 LanceDB Hybrid Recall 成熟后的非必要双轨优化，已删除相关 runtime 配置、代码和测试。
- 保留风险：Web 示例普通 memory ingest/list/delete 仍是 demo/BFF 后置形态；外网生产暴露前必须加网关鉴权。原子 rebuild、批量 embedding、LanceDB where 下推、完整 repair queue 归 P4 平台化。

### 第二轮代码审查记录（P0-P3-1）

- 已确认：本地轻量 `semantic_recall` fallback 已裁剪干净，runtime/config/tests 中无残留，仅文档保留裁剪记录。
- 已修复：Web Hindsight 按行删除改为调用 `HindsightStore.delete_line()`，与 append 共用文件锁，并同步清理 JSON sidecar 与 vector sidecar。
- 已修复：`agent-os-runtime hindsight-index vector-rebuild` 等运维命令在返回 `status=error` 时会以非 0 退出码结束，避免自动化误判。
- 已修复：`AsyncReviewService.submit_and_wait()` 返回结构化状态；Web session end 不再固定报告 `completed`，而是透传 `ok/skipped/timeout/error` 等状态。
- 已修复：TaskMemory SQLite 连接策略已与 Ledger 对齐，补充 busy timeout / WAL / NORMAL synchronous，降低多 worker 下锁冲突概率。
- 已修复：`AGENT_OS_HINDSIGHT_VECTOR_CANDIDATE_LIMIT` 显式控制向量召回候选池大小，避免隐藏在构造函数默认值里。
- 保留风险：向量检索仍是 over-fetch 后内存 metadata 过滤，严格过滤/大库场景可能候选不足；LanceDB where 下推归后续平台化/性能治理。

2. **真实评测闭环平台**：未执行。
   - 把 CI、golden tests、人工验收、业务指标接入 outcome/negative evidence/recurrence。
   - 建立经验是否提升执行正确率的离线评估集。

3. **长期 summary/index 体系增强**：未执行。
   - 按 client/user/task/skill/deliverable 建立稳定派生 summary/index。
   - Summary 仍只作为检索辅助，不自动覆盖原始 Hindsight，不进入 Mem0。

### P4：平台化与安全生命周期（只记录，不执行）

P4 是平台治理层，当前阶段不执行。

1. **统一文件治理 / watcher**：未执行。
   - 为 Hindsight JSONL、sidecar index、ledger 等文件提供统一清理、重建、校验命令。
   - 可选 watcher 监听外部改写并自动清理 stale sidecar。

2. **安全与数据生命周期平台化**：未执行。
   - 对 JSONL、sidecar、ledger、Asset Store 等统一做权限、脱敏、加密、保留期和删除证明。

3. **完整审计平台**：未执行。
   - duplicate attempt、policy audit、admin 操作、外部反馈回写统一进入可查询审计平台。

### 已写代码归属与回退结论

- 保留：Policy/ledger 审计、AsyncReview 结构化 outcome 与质量信号、Hindsight sidecar index、受控 debug 入口、轻量近似簇、两阶段预算。这些均服务 P1/P2 的最小治理能力。
- 不回退：当前没有发现“只服务 P3/P4、没有 P1/P2 价值、且保留会带来明显治理或安全成本”的代码。
- 降级归档：真实 CI/业务指标反馈、watcher、统一文件治理、安全生命周期平台、完整审计平台只记录到 P3/P4，不继续编码。

