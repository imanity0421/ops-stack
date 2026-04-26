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

