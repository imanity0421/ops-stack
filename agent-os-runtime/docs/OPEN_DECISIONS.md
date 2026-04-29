# Open Decisions

> **本文定位**：[ARCHITECTURE.md](ARCHITECTURE.md) 沉淀的是 25+ 轮挑刺仍屹立的稳定层；本文沉淀的是**未稳定 / 待回答**的开放问题。
>
> **三类问题**：
>
> - **A 类（工程验证）**：架构上已经知道方向，但具体写法 / 字段集 / 阈值需要写代码时回答。**回答方式**：Stage 2-6 代码 PR + GC trace。
> - **B 类（总设计师决策）**：本质不是架构对错问题，而是产品边界 / 优先级问题。**回答方式**：用户在 OPEN_DECISIONS 上明确选择。
> - **C 类（已暂时回答但保留挑刺空间）**：当前选了 A 路线，B 路线被拒绝，但保留未来 B 路线再次被考虑的可能性。**触发再开**：必须伴随 PoC 代码或 GC 失败 trace。
>
> **决策出口规则**：
>
> 1. A / B 类一旦回答清晰，对应内容沉淀到 [ARCHITECTURE.md](ARCHITECTURE.md)，**本表移除该条**。
> 2. C 类原则上不再讨论，除非伴随硬证据再开。
> 3. 不允许"长期挂着、反复辩论"——挂超过 3 个月仍无证据回答，默认按当前推荐答案永久关闭。

---

## A. 工程验证待回答（写代码时回答）

### A2：Deliverable Lifecycle 字段定义归 Stage 2 还是 Stage 5

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.3 已定——版本控制（current / previous / final）是池 2 的字段语义，归 MA；业务消费（如 `/skill deliverable promote`）归 SR。1.4 已预留 `/artifact finalize <id>` 命令名（Stage 5 实现）。

**待回答**：

- Stage 2 是否提前定义 `subkind: "draft" | "current" | "final"` 字段并接通 `/artifact` 命令的版本切换语义，还是 Stage 5 才接业务命令。
- 池 2 schema 中 `subkind` 是 enum 还是开放 string（开放允许 skill 自定义子分类）。
- `/artifact finalize` 业务字段：是否需要 `final_at` / `final_session_id` / `final_by` 等审计字段；finalize 是否阻止 update（强只读）还是仅作标签提示。
- 单 task 是否允许多 final（不同 deliverable 类型，如"方案 + 附件"）还是强制单一 final。

**回答方式**：Stage 2 PR 决定 subkind 字段是否预留；Stage 5 Battle 4 决定业务命令字段集与多 final 策略。

**当前推荐**：Stage 2 预留 subkind 开放 string + `previous_subkind_history` 列；Stage 5 接业务命令——`final_at` 必加、`final_session_id` 必加、`final_by` 可选；`finalize` 后 artifact 强制只读直到 `/artifact unfinalize`；单 task 默认允许多 final（用户场景常见"主方案 + 配套素材"）。

**Battle 6 收口备注（2026-04-29）**：经 Stage 2 Battle 2-4 实现复核，当前 ArtifactStore 未预留 `subkind` / `previous_subkind_history` DB 字段；只在 prompt replacement 层使用 `kind="tool_result" | "source" | "deliverable"`，并用 `stable_key` 保证重复内容复用。结论：不回补 Stage 2 schema；Stage 5 `/artifact finalize` 落地时再通过迁移引入开放 string `subkind` 与 final 相关审计字段。

### A3：池 2 召回不物理隔离的具体权重 / 阈值

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.3 已定多级隔离规则——branch session 默认互不召回（仅当前 session 及祖先链）、跨 task 召回必带 prompt 装配层硬隔离标签（`<artifact ref task_id=... cross_task=true>` + 风险提示文本）、archived 默认排除。chunk lazy 建立时机也已上提到总纲。

**待回答（仅剩参数级）**：

- "权重略低"的具体公式（cross_task 系数 0.5？0.7？随 task 距离衰减？）。
- 是否需要 `--include-other-tasks` flag 显式开启跨 task 召回，还是默认召回时统一带回但 prompt 标签 + 权重降权。
- prompt 风险提示文本的精确措辞（中英？模板化？是否随 task 类型微调）。

**回答方式**：Stage 2 上线 artifact registry 后跑 5-10 个 business_writing 真实 case，看跨 task 命中是否真有用；Stage 4 resume 跑通后做 A/B 对比（含 / 不含风险提示文本对 LLM 数字锚定的抑制效果）。

**当前推荐**：默认仅当前 task；显式 flag 跨 task；权重系数 0.6 起步；archived 默认排除；风险提示文本英文模板优先（LLM 训练语料分布偏向英文指令）。

**Battle 6 路径决策（2026-04-29）**：Battle 6 选择最小路径，只统计已进入 prompt / trace 的 artifact ref 与 artifactized signal，不主动查 ArtifactStore、不实现跨 task artifact 召回、不决策跨 task 权重公式。A3 的召回权重 / flag / 风险提示文本仍留到 Stage 4 resume 与真实 cross-task case 后回答。

**Stage 4 启动前确认（2026-04-29，Phase 8）**：A3 仍按 Battle 6 路径决策延续——Stage 4 五个 battle 不实现跨 task artifact 召回、不引入权重公式 / `--include-other-tasks` flag / 跨 task 风险提示文本。Stage 4 resume 装配仅消费当前 task 及祖先链 artifact；跨 task 召回推到 Stage 5 真实 business_writing 出现 cross-task case 后回答（与 F4 暂缓项一致）。

### A4：task_table 是否真的够 5 字段

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.4 已定 5 字段（task_id / name / status / created_at / current_main_session_id）。

**待回答**：

- 实际 Stage 2 跑代码后是否需要补字段（如 `last_active_at` 加速归档判断、`tag` 支持任务分类、`parent_task_id` 支持 task 树）。
- `current_main_session_id` 是否要扩展为 `session_history: list[str]` 以支持更精细的分支管理。

**回答方式**：Stage 2 PR 实现 task entity v0 后跑 1-2 周真实任务，看哪些字段是"必须的"哪些是"加上更舒服但其实可以查 session 表得到"。

**当前推荐**：5 字段先发；扩展字段进 [ARCHITECTURE.md](ARCHITECTURE.md) 1.4 时回填。`parent_task_id` 与 task 树相关，归 B4 决策。

**Stage 4 启动前确认（2026-04-29，Phase 8 落地修正版）**：

经实测核查 [task_memory.py:138-146 / 425](../src/agent_os/agent/task_memory.py)，`sessions` 表已存在并已维护 `session_id` / `task_id`-equivalent (`active_task_id`) / `last_active_at`-equivalent (`updated_at`) 等字段。Stage 4 branch 派生字段落地路径：

- **`tasks` 表保持 5 字段不变**（兑现 [ARCHITECTURE.md](ARCHITECTURE.md) §1.4 与 D2 承诺：branch / CoW 不污染 task entity v0 5 字段）。
- **扩展现有 `sessions` 表 +2 列**：仅新增 `parent_session_id TEXT NULL` + `branch_role TEXT NULL`（取值 `main` / `branch` / NULL=root）。
- **复用映射不新增**：
  - `task_id` ← 复用现有 `active_task_id`（已是 task 归属字段）。
  - `last_active_at` ← 复用现有 `updated_at`（已由 [task_memory.py:425](../src/agent_os/agent/task_memory.py) `UPDATE sessions SET updated_at = ?, active_task_id = ? WHERE session_id = ?` 主动维护）。
  - `is_main` ← 反查 `tasks.current_main_session_id == sessions.session_id`（语义单源，避免 main 切换时双写漂移）。
- **拒绝路径**：不新建 `task_sessions` 投影表（A4-i 已被实测推翻：会引入 `sessions` ↔ `task_sessions` 双表一致性维护负担，且 `active_task_id` 已实现等价语义）。
- **拒绝字段**：不新增 `task_id` / `last_active_at` / `is_main` 三列（与已有字段语义重复，违反 §3.6 反模式 "字段重复"）。

理由：扩展现有表 +2 列是改动最小、复用最充分、单源最强的路径——B5.c connect/fork 阈值判断（`updated_at` 距今 < 30 分钟 + token 占用）、Battle 2 branch tree 遍历（`parent_session_id` 反向链）、Battle 3 CoW 触发（`originating_session_id` vs `current session_id` 对比）所需字段全部齐备。

### A5：CTE 内 task 级编排函数 / 类边界

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.1 跨模块编排归属规则已定——task 级编排归 CTE，表现层只调单个高层 API（如 `CTE.resume_task(task_id)`），由 CTE 内部 fan-out 到 MA / SR / ER。

**待回答**：

- CTE 内承担 task 级编排的代码组织：单个 `TaskOrchestrator` 类承担 resume / branch / compact 协调？还是按命令拆 `resume_task.py` / `branch_task.py` / `compact_task.py` 平铺函数？
- 编排函数对其他模块的调用是否走"模块对外 API（如 `MA.fetch_task_final_state`）"还是直接 import 子模块函数。前者更可测试，后者更直接。
- CTE → ER 启动新 session 的入口（`ER.run_session(...)` 还是直接调 agno）。

**回答方式**：Stage 4 写 `/task resume` 时落地——这是第一个真实跨 4 模块编排的命令。Stage 5 写 `/skill compose context_pack` 时再验证 SR 级编排是否结构同构。

**当前推荐**：先按命令拆平铺函数（避免过早抽象 Orchestrator 类）；模块间走对外 API 不直接 import 子函数；ER 包一个 `start_resumed_session(prompt, session_meta)` 高层入口供 CTE 调。

**Stage 4 启动前路径决策（2026-04-29，Phase 8）**：采纳 "当前推荐"。Stage 4 Battle 1-2 落地路径：

- 在 `agent_os/cte/` 下新增 `resume_task.py` / `branch_task.py` 两个平铺模块（**不**抽象 `TaskOrchestrator` 类）。
- CTE → MA / SR / ER 均走对外 API：
  - `MA.fetch_task_final_state(task_id) -> TaskFinalState`（实时合成 `CompactSummary` + uncompacted tail + `current_deliverable` + `pinned_refs`）。
  - `SR.get_voice_pack(task_id) -> VoicePack | None`（None fallback 见 S3 / F2）。
  - `ER.start_resumed_session(prompt, session_meta) -> SessionId`（接 agno；不在 CTE 直接 import agno）。
- compact 协调仍归 ER 自身（Stage 3 已落地）；CTE 不接管。
- Stage 5 引入 `/skill compose context_pack` 时再验证 SR 级编排是否结构同构；不预先抽象 `SkillOrchestrator`。

### A6：超长 deliverable 的章节切分策略

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 3.3 已定 resume 装配降级链 `full → digest+tail_3 → digest+tail_2 → digest+tail_1 → digest_only`，digest 始终保留作全局梗概锚点；§1.3 已定 artifact 写时必生成 digest。

**待回答**：

- 当 `current_deliverable` 长到一定阈值（如 1 万字 / 5 万 token）时，是否自动按章节切多个 artifact，让 resume 时只 inline "当前编辑章节" + 旧章节走 ref？
- 切章节边界谁判定：用户显式 `/deliverable split` 命令、LLM 在 compact 时顺手切、还是按 token 阈值机械切？
- `tail_3 / tail_2 / tail_1` 的精确粒度（章节 / 段 / 句）——business_writing 倾向章节级、代码 skill 待定。
- `digest_only` 兜底是否要追加"已出现专有名词集合"以减少续写幻觉。

**回答方式**：Stage 4 写 `/task resume` 后跑真实 1.5 万字以上 business_writing 任务（白皮书 / 长篇市场分析），让真实 deliverable 长度自然推动粒度选择。

**当前推荐**：

- `tail_n` 默认按章节级（business_writing 场景）。
- 当 `current_deliverable` 超 1 万字时建议用户 `/deliverable split` 显式切章节；不做自动切（避免边界判错引发用户困惑）。
- `digest_only` 兜底必含 H2 标题列表 + 已出现专有名词集合（300 字内）。
- 这些行为属于 SR.business_writing 的 deliverable lifecycle 装配，不污染 CTE 通用降级机制。

**Stage 4 启动前确认（2026-04-29，Phase 8）**：A6 章节切分策略 Stage 4 不做。Stage 4 resume 装配仅复用 [ARCHITECTURE.md](ARCHITECTURE.md) §3.3 现有降级链 `full → digest+tail_3 → digest+tail_2 → digest+tail_1 → digest_only`，不引入 `/deliverable split` 命令、不引入章节级 `tail_n` 切分粒度、不引入 H2 标题列表 + 已出现专有名词集合的 `digest_only` 兜底字段。完整 A6 实现推到 Stage 5 真实 1.5 万字以上 business_writing 任务（与 F4 暂缓项一致）。

### A7：compact schema fragment 契约（签名已定，参数级残留待回答）

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 3.2 已定机制——SR 通过 schema fragment 注册接口向 CTE 暴露 business_writing_pack / skill_state 的 Pydantic / JSON Schema；CTE 在 compact LLM 调用前合成完整 schema。机制级已上文档，本条只剩接口签名级。

**Stage 3 已回答（已落地并沉淀回 ARCHITECTURE §3.2）**：

- 接口签名固定为 `get_compact_schema_fragment() -> type[BaseModel]`。
- 首版强制 single-skill-active；不支持多 skill 同时活跃合成。
- schema 版本由 `CompactSummary.schema_version` 字段承载，不额外引入 schema metadata 二元组。

**回答方式**：Stage 3 写第一个 compact 实现时落地；Stage 5 引入 business_writing skill 时验证接口在跨 skill 类间的复用性。

**当前推荐**：启动时注册（让 DI 容器主导）；签名为 `Protocol.get_compact_schema_fragment() -> Type[BaseModel]`，schema 版本管理交给 Pydantic model 内部 `schema_version` 字段；多 skill 暂不支持（第一个实现强制 single-skill-active）。

**Stage 3 收口备注（2026-04-29）**：已按用户确认的推荐路径落地 `SkillSchemaProvider` Protocol，签名为 `get_compact_schema_fragment() -> type[BaseModel]`。Stage 3 首版强制 single-skill-active；`business_writing_pack` / `skill_state` 默认 `null`，Stage 5 再由真实 skill fragment 验证复用性。

**仍保留的参数级残留（待 Stage 5/7+ 或性能压力触发）**：

- 多 skill 同时活跃时如何合成（concat fields？嵌套 dict？）——仅在出现 Multi-Skill 真实需求时再开。
- schema fragment 缓存策略（首字延迟 vs schema 体积）——仅在 compact LLM schema 明显成为瓶颈时再开。
- `business_writing_pack` / `skill_state` 的 size 阈值与降级策略（与 [ARCHITECTURE.md](ARCHITECTURE.md) §3.2 Layer 2/3 size 约束一致）——属于参数级，随真实 PR/GC trace 微调。

### A8：pinned_refs 填充 / 取消 pin 策略

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 3.2 已在 CompactSummary.core 预留 `pinned_refs: list[str] | null` 字段位（system-state，由代码层维护）；§3.3 已定 resume 时强制 inline。字段位级已上文档，本条只剩填充策略。

**待回答**：

- pin 触发时机：仅用户显式 `/memory pin <id>` / `/asset pin <id>`？还是 agent 在某些场景（如用户连续两次纠正同一问题）建议用户 pin？
- pin 数量上限——避免冷启动 prompt 被 pin list 撑爆。
- pin 是 task 级（仅本 task 资料 pin）还是跨 task 级（全局 pin）？
- 取消 pin：仅用户显式 `/memory unpin`，还是某些场景自动取消（如 task 归档）？
- pin 优先级与召回排序的关系（pinned_refs inline 时是否还参与 LLM 召回打分）？

**回答方式**：Stage 6 Memory & Voice Stabilization 时落地——届时已有真实 memory candidate 流，pin 频率与体感能被实测推动。

**当前推荐**：

- 仅用户显式 pin / unpin（不做自动 pin）。
- 单 task pin 上限 5 条 memory + 3 条 asset（约 1500 token，冷启动可承受）。
- pin 是 task 级；跨 task pin 通过 asset `pinned_for_workspace` 已有字段实现，不重复机制。
- task 归档不自动 unpin（保留用户语义），但 archived task 的 pin 不参与新 task 的召回。
- pinned_refs inline 时**不再参与召回打分**（已强制在场，再打分会双倍占预算）。

**Stage 4 启动前确认（2026-04-29，Phase 8 同日补丁）**：A8 填充策略仍归 Stage 6（与"回答方式"一致），Stage 4 不实现 `/memory pin` / `/asset pin` / `/memory unpin` 命令。Stage 4 Battle 1 的 `/task resume` v0 final_state 实时合成（见 F1）对 `pinned_refs` 字段**只消费不填充**：

- 读取已存在的 `CompactSummaryCore.pinned_refs` 字段：默认空列表 → resume 装配跳过该 inline 段（不报错、不 trace skipped 标志，因为字段位级是默认空而非 None）；非空 → 按 [ARCHITECTURE.md](ARCHITECTURE.md) §3.3 强制 inline，不参与召回打分。
- 不引入填充入口：Stage 4 不实现 pin / unpin 命令，不实现 agent 自动建议 pin 逻辑；Stage 6 真实 memory candidate 流稳定后再实现完整生命周期。
- 与 S3 voice_pack=None fallback 同构：都是"Stage 4 装配只消费上游已存在字段，不强制非空、不实现填充"原则。

---

## B. 总设计师决策待回答（用户决策）

### B1：Voice Pack 是 Stage 5 还是更早

**背景**：GPT 多轮挑刺要求把 Voice Pack 提到 Stage 2 或 Stage 3，理由是"商业写作不带 voice 等于残次品"。

**当前选择**：留 Stage 5 Battle 1（First Skill SOTA Loop 内）。

**理由**：

- [ARCHITECTURE.md](ARCHITECTURE.md) 3.2 的 `CompactSummary` schema 三层结构中，整个 `business_writing_pack` 是 nullable 子层，Stage 2-4 默认全 null，没有"先有数据结构再有数据填充"的真悖论。
- Stage 2-4 的 GC 断言 `business_writing_pack.brand_voice` 字段时按 nullable 处理（Stage 3 不强求；Stage 5 强求非空），见 3.5 GC 分级规则。
- 更早做 voice pack 会导致 Stage 2-4 的 GC 在没有真实 skill / context pack 的情况下"为测 voice 而 mock voice"，违背"先有货再建物流"。

**反向选项（如果用户改主意）**：

- B1.a：Stage 3 引入"轻 voice 占位"——只是字段默认值，不写真业务，仅供 schema 验证。
- B1.b：Stage 5 提前到 Stage 4 之后立刻做（合并 Stage 4 和 Stage 5 中间过渡期）。

**决策门槛**：除非用户明确要求，否则保持当前选择。

### B2：是否做 SubAgent ContextSandbox

**背景**：Claude Code Harness 有完整的 SubAgent ContextSandbox（隔离上下文、独立工具集）。原 V1 计划独立 stage，V2 推到 Stage 7+。

**当前选择**：暂不做，留 Stage 7+ 可选项。

**理由**：

- 个人级 SOTA 的核心痛点是"主线 agent 把一个 skill 在一个长任务里做到底"，不是"调度一群 SubAgent"。
- SubAgent 引入 inter-agent context boundary、工具子集、子任务 summary、子产物归档等大量复杂度，主线 agent 没稳定时引入会爆炸。
- 真用例（如 Reviewer SubAgent / Research SubAgent）只在 Stage 5 真实跑过 business_writing 后才知道是否真有需求。

**反向触发条件**：Stage 5 后的真实 business_writing 任务出现"主线 agent 上下文爆炸但任务本身可拆"的硬证据时，再启 Stage 7。

### B4：Task 实体的产品语义边界

**背景**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.4 task_table 极简 5 字段。但 task 实体可以扩展的语义维度很多。

**待决策（用户拍）**：

- B4.a：是否需要 `tag` 字段（任务分类，"商业 / 运营 / 写作"）。
- B4.b：是否需要 `parent_task_id`（task 树支持子任务）。当前推荐**不做**——会引入治理复杂度。
- B4.c：是否需要 `due_date`（用户驱动的 deadline）。当前推荐**不做**——agent 自己不催办。
- B4.d：是否需要 `participants`（多人协作）。**永远不做**——超出个人级范围。

**当前推荐**：B4.a 可加（轻量、用户主动填），其余全部不做。

**已确认并沉淀（2026-04-29）**：

- B4.a：Stage 2 v0 不加 `tag`（真实分类痛点出现前不加；未来若需要按增量字段迁移加入）。
- B4.b：不加 `parent_task_id`（task 树治理复杂度过高，超出个人级主线）。
- B4.c：不加 `due_date`（agent 不催办，deadline 属用户外部流程）。
- B4.d：永远不加 `participants`（多人协作超出个人级范围）。

上述结论已沉淀到 [ARCHITECTURE.md](ARCHITECTURE.md) §1.4（task_table 字段集判定）与 §3.6（反模式清单）；本条仅保留作历史备查，原则上不再作为待决策项反复讨论。

**B4.a 已落地（2026-04-29）**：Battle 1 实际实现与 [ARCHITECTURE.md](ARCHITECTURE.md) 1.4 保持一致，`tasks` 表和 `TaskEntity` 均为 5 字段：`task_id` / `name` / `status` / `created_at` / `current_main_session_id`。Stage 2 不增加 `tag`；若真实 task 列表出现分类痛点，再作为增量字段重新打开。

### B5：断线重连 vs 跨天恢复的命令分离

**背景**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.4 当前 `/task resume` 总是开新 session（强制 fork）。但实际场景里"短暂断线（倒杯水 / WebSocket 断开 5 分钟）"和"跨天恢复（关电脑明天再来）"是两种语义：

- **断线重连**：当前 session 还活跃、未爆 token、未 archive——直接连回去最自然；`/task resume` 强制 fork 会丢掉最后几轮还没 compact 的 raw history（如刚跟 agent 讨论"第三段加笑话"，断线后 fork 拉的 summary 没有这条记录）。
- **跨天恢复**：当前 session 已断（关机、几小时不活动、token 超阈值）——必须基于 final state 开新 session，agno raw history 即使保留也不应直接复用（context 重建质量更可控）。

**待决策（用户拍）**：

- B5.a：**双命令路径**——`/task connect <task_id>`（连回 current_main_session 的 raw session）+ `/task resume <task_id>`（强制开新 session）。语义清晰但增加用户认知负担。
- B5.b：**单命令 + flag**——`/task resume <task_id>` 默认行为按"是否还活跃"自动判断，`--force-fork` flag 强制开新。简洁但隐式行为可能引发 surprise。
- B5.c：**单命令自动判断**——只有 `/task resume`，由 CTE 内部按一组规则（session 距离上次活跃时间 / token 占用率 / 是否 archive）决定 connect 或 fork，trace 中标记选择路径。最少认知负担、但隐式逻辑最强。

**默认行为定义**（无论选哪个路径，规则一致）：

- 当前 session 距上次活动 < 阈值 X（推荐 30 分钟）且 token 占用 < 80% 阈值 → 倾向 connect。
- 否则 → 倾向 fork。

**当前推荐**：B5.c（单命令自动判断），trace 中显式记录决策路径与触发规则；保留 `--force-fork` 与 `--force-connect` flag 用于 override。理由：用户大多数场景不应该被迫想"我现在是 connect 还是 resume"；让系统按客观信号决定是 SOTA 体感。

**反向选项**：若 B5.c 实测出现"系统判错率 > 10%"或用户明确表达"我想要更显式的控制"，回退到 B5.a 双命令。

**Stage 4 启动前确认（2026-04-29，Phase 8）**：采纳 B5.c 单命令路径。Stage 4 Battle 1 落地：

- 仅一个命令 `/task resume <task_id>`（不引入 `/task connect`）。
- CTE 内部按以下规则自动判断 connect 还是 fork：
  - **倾向 connect**：当前 main session 距 `updated_at` < 30 分钟 **且** token 占用 < 80% **且** session 未 archive。
  - **倾向 fork**：上述任一条件不满足。
- 保留 `--force-fork` / `--force-connect` flag 用于 override（Battle 1 必须实现）。
- trace 必须显式记录决策路径（`resume_diagnostics.connect_or_fork = "connect" | "fork"` + `decision_reason: list[str]` + `forced_by_flag: bool`），见 Battle 4 `/context` 集成。

回退条件复用上方"反向选项"——Battle 1 落地后若实测系统判错率 > 10%，再回退到 B5.a 双命令；这一兜底不影响 Battle 1 当前实施。

---

## C. 已暂时回答但保留挑刺空间

### C1：不做 TTL 自动清理

- **当前回答**：不做。所有删除必须用户显式触发（`/task clean` / `/blob gc --orphan` 只列不删）。
- **保留空间**：硬盘真的爆了再考虑半自动 cleanup，但需要先有真实硬盘压力 trace。
- **再开门槛**：用户运行 ≥ 3 个月后真实硬盘 / LanceDB 容量超阈值。

### C2：不做自动 task boundary 检测

- **当前回答**：不做。开新 task 必须用户显式 `/task new`。
- **保留空间**：等用户实测主诉"忘记开新 task" 而非"系统判错 boundary" 后再考虑。
- **再开门槛**：用户在 ≥ 5 个真实任务后明确报告"经常忘记切 task"。

### C3：不做 Memory Curator Agent

- **当前回答**：不做。Memory candidate 由用户 `/memory candidates review` 显式入库。
- **保留空间**：Stage 6 Memory candidate 流稳定后，看是否累积量级到值得自动整理。
- **再开门槛**：用户累积 ≥ 200 条 memory 且报告"找不到相关 memory"。

### C4：不做服务端 context management

- **当前回答**：永远不做。
- **理由**：与 Anthropic API 强绑定、违背"个人级、可本地、可换模型"原则。
- **不再开**：本条已永久关闭，不留挑刺空间。

---

## D. Stage 2 Battle 排序（结论已定，待执行）

> **本章性质**：[ARCHITECTURE.md](ARCHITECTURE.md) 第 4 节给出了 Stage 2 一句话承诺（"长内容 artifact 化 + 生命周期闭环 + Task 实体落地"），但单一承诺背后是 6 个独立可发布的 battle。本章固化 battle 顺序与依赖关系，避免 Stage 2 实际执行时塞成一个不可 review 的大 PR。
>
> **与 A / B / C 的关系**：本章不是"待回答的开放问题"，结论已定；只是结论的执行细节归 OPEN_DECISIONS 而非 ARCHITECTURE（避免污染稳定层）。

### D1：6 个 Battle 顺序

| # | Battle | Status | 核心交付 | 依赖前置 |
| --- | --- | --- | --- | --- |
| 1 | **Task Entity v0** | done @ 2026-04-29 (commit `cca7273`) | `task_table` 5 字段 schema、`/task new` / `/task archive` / `/task unarchive` / `/task list`；单 session 1:1 跑通 | 无 |
| 2 | **Artifact Registry v0** | done @ 2026-04-29 (commit `cca7273`) | 池 2 字段扩展（`kind=artifact` 区分 + `task_id` 外键）、artifact 显式产出与引用、`<artifact ref>` prompt 装配 | Battle 1 |
| 3 | **Tool Result Artifactization** | done @ 2026-04-29 (commit `e5000cd`) | 长 tool result 自动外置为 artifact、`<artifact ref>` 替代原文进 history、prompt budget 内只保留摘要 + ref | Battle 2 |
| 4 | **Long Source Artifactization** | done @ 2026-04-29 (commit `2e24307`) | 用户上传长文档 / agent 产出长文本自动 artifact 化、原文层 SQLite 写入 + 异步 digest 生成 | Battle 2 |
| 5 | **Artifact Lifecycle Commands** | done @ 2026-04-29 (commit `3241da8`) | `/artifact list` / `/artifact show` / `/artifact archive` / `/blob gc --orphan` 只列不删、archive 软删除 | Battle 2-4 |
| 6 | **Trace + `/artifact` + `/context` Integration** | done @ 2026-04-29 (commit `c305d16`) | trace 字段持续记录 artifact 引用、`/context` 显示 artifact 占比、artifact 召回作 trace 归因 | Battle 5 |

**D1 Status 维护规则**：

- D1 是 Stage 2 当前研发进度的唯一一跳入口；串行 agent 开工前必须先读本表。
- 每个 battle 完成时只更新对应行 `Status`，并在 [CHANGELOG.md](CHANGELOG.md) 记录已完成交付；不要把子任务流水账写进本表。
- `Status` 建议值：`todo` / `in-progress` / `done-local @ YYYY-MM-DD (tests passed, commit pending)` / `done @ YYYY-MM-DD (commit <sha> / PR #N)` / `blocked: <一句话原因>`。`done-local` 不具备跨机器 handoff 效力；可接续状态至少需要 commit，最好已 push。
- 若 `done` 后发现需要回滚，将对应 battle `Status` 回退到 `todo` 或 `in-progress` 并写明原因，同时在 [CHANGELOG.md](CHANGELOG.md) 增加修复条目引用 revert commit / PR。
- battle 边界、顺序或依赖变化时，才修改 D2 / D3 或回到 A / B / C 记录依据；不要为进度更新修改 [ARCHITECTURE.md](ARCHITECTURE.md)。

### D2：Branch / CoW 推到 Stage 4

- **结论**：Stage 2 **不实现** branch session 与 Copy-on-Write，相关代码推到 Stage 4（`/task resume` + `/task branch` 一起落）。
- **依据**：
  - branch / CoW 涉及"多 session 共享 task"的复杂状态联动——Stage 2 主路径是单 session 跑通，引入 branch 会让 Battle 1（Task Entity v0）从 1 周变 3 周。
  - CoW 契约已写在 [ARCHITECTURE.md](ARCHITECTURE.md) 1.3（"分支跨 session 修改强制 CoW"）——契约先在、实现后跟是合理工程节奏。
  - Stage 2 的 Task Entity v0 仅需 `task_table` 5 字段（含 `current_main_session_id`），暂不需要 branch session 的派生字段（如 `parent_session_id`、`branch_role`）。
  - Stage 4 引入 branch 时，artifact 的 `originating_session_id` 字段是**首次引入并启用**，不存在"先建字段后启用"的历史包袱。
- **Stage 2 的 task_table 5 字段保持不变**——为 Stage 4 branch 留出扩展空间不会让 Stage 2 schema 复杂化（branch 相关字段是 Stage 4 增量）。

### D3：执行节奏建议

- **Battle 1 + 2 必须同 PR / 紧邻 PR**：Task Entity 与 Artifact Registry 的 `task_id` 外键是同一表关系的两端，分两 PR 会产生孤儿外键空窗期。
- **Battle 3 与 4 可并行**：tool result 与 user upload 走同一 artifactization 路径，但触发源不同——一个由 ER 内部触发，一个由表现层 / CLI 触发；可由不同人并行实现。
- **Battle 5 必须在 6 之前完成**：lifecycle 命令是 trace / context integration 的可观测入口。
- **Battle 6 收口**：Stage 2 的 GC 集中在本 battle 验证（artifact 引用持续在 trace 中、`/context` 占比正确、跨 task 召回不串味）。

### D4：暂缓但不可遗忘的研发管理项

- **PR template**：暂缓创建 `.github/PULL_REQUEST_TEMPLATE.md`。若开始频繁通过 PR 串行交接，或出现 Reference Check / 测试命令 / D1 Status / CHANGELOG 漏填，再创建模板；模板应包含 Battle / Scope、Claude Code Reference Check、Test Plan、CHANGELOG / D1 Status checklist。
- **GC_SPEC.md**：已在 Battle 6 创建最小 [GC_SPEC.md](GC_SPEC.md)，承载 Stage 2 artifact trace / `/context` / lifecycle 的 GC1-3 字段级断言骨架；Stage 3 compact GC 再扩展断言强度矩阵。
- **Stage 2 baseline trace**：已在 Battle 6 收口补跑 2 个 artifact diagnostics baseline（长 tool result artifactization、pending artifact ref `/context`），记录在 [GC_SPEC.md](GC_SPEC.md)；不写入 [CHANGELOG.md](CHANGELOG.md)。

---

## E. Stage 3 Compact 执行状态

> **本章性质**：Stage 3 的一句话承诺是"长会话可 compact 并以 `CompactSummary` schema 恢复目标，Task Loop 核心闭环成立"。本章只记录执行状态；架构契约仍以 [ARCHITECTURE.md](ARCHITECTURE.md) 3.2 / 3.5 为准。

| # | Battle | Status | 核心交付 |
| --- | --- | --- | --- |
| 1 | **CompactSummary v1 Schema** | done @ 2026-04-29 (commit `98f7953`) | `CompactSummaryCore` / `CompactSummary` schema、system-state 与 LLM-generated state 分离、A7 `SkillSchemaProvider` Protocol |
| 2 | **Compact Store + Manual CLI** | done @ 2026-04-29 (commit `98f7953`) | `compact_summaries` SQLite 表、`compact run/show` CLI、JSON/Markdown 可观测 |
| 3 | **Compact Prompt + Fallback** | done @ 2026-04-29 (commit `98f7953`) | 有 key 时 JSON compact prompt；无 key / 失败时 deterministic fallback |
| 4 | **Context Rehydration v0** | done @ 2026-04-29 (commit `98f7953`) | `ContextBuilder` 注入 `<compact_summary>`，`/context` 输出 `compact_diagnostics` |
| 5 | **Budget Suggestion Mode** | done @ 2026-04-29 (commit `98f7953`) | `compact_suggested` 只作为 diagnostics signal，不自动 compact |
| 6 | **GC4/GC5 Stage3 收口** | done @ 2026-04-29 (commit `98f7953`) | [GC_SPEC.md](GC_SPEC.md) 追加 GC4 / GC5 Stage3 字段级断言 |

---

## F. Stage 4 Battle 排序（结论已定，待执行）

> **本章性质**：[ARCHITECTURE.md](ARCHITECTURE.md) 第 4 节给出了 Stage 4 一句话承诺（"用户离开后 `/task resume` 两轮内复现工作面，Task Loop 成为跨会话稳定容器；Golden Case 同步收口为贯穿基线"），但单一承诺背后是 5 个独立可发布的 battle。本章固化 battle 顺序与依赖、Stage 4 启动前已敲定决策摘要、暂缓项；不再因进度变化回填 [ARCHITECTURE.md](ARCHITECTURE.md)。
>
> **与 D / E 章节同构**：D（Stage 2 Battle 排序）/ E（Stage 3 执行状态）已建立 "结论已定的执行细节归 OPEN_DECISIONS、不污染 ARCH 稳定层" 的惯例，本章延续。

### F1：5 个 Battle 顺序

| # | Battle | Status | 核心交付 | 依赖前置 |
| --- | --- | --- | --- | --- |
| 1 | **`/task resume` v0** | done @ 2026-04-30 (commit `c53ad7f`) | resume 命令、final_state 实时合成（`CompactSummary` + uncompacted tail + `current_deliverable` + `pinned_refs`）、tail history 纯文本投影；`voice_pack` 为 None 时跳过该 inline 段并 trace 标 `voice_pack_skipped=true`；按 B5.c 单命令自动判断 connect/fork（默认阈值 30 分钟距上次活动 / 80% token 占用，保留 `--force-fork` / `--force-connect` flag override） | Stage 3 `CompactSummary` v1 |
| 2 | **`/task branch` v0** | done-local @ 2026-04-30 (pytest + ruff passed, commit pending) | branch 命令、**扩展现有 `sessions` 表 +2 列**（`parent_session_id TEXT NULL` + `branch_role TEXT NULL` 取值 `main` / `branch` / NULL=root）；`task_id` / `last_active_at` / `is_main` 均复用现有字段不新增（详见 F2）；main 与 branch session `CompactSummary` 互不污染 | Battle 1 |
| 3 | **Artifact CoW v0** | todo | `originating_session_id` 在 `ArtifactStore` 启用、CoW 触发规则（同 session 原地 update vs 跨 branch 强制复制）、原子事务边界（与 [ARCHITECTURE.md](ARCHITECTURE.md) §1.3 一致） | Battle 2 |
| 4 | **Resume Trace + `/context` 集成** | todo | resume 装配 trace 字段、`/context` JSON 显示 `resume_diagnostics`（含 connect/fork 决策路径、`deliverable_inline_level` 命中档位、`voice_pack_skipped` 标志）、降级链 `full → digest+tail_n` 命中观测 | Battle 1 |
| 5 | **Golden Case GC-Resume 收口** | todo | [GC_SPEC.md](GC_SPEC.md) 追加 GC-Resume Stage4 字段级断言；2-3 个真实 baseline trace（隔天 resume 命中 L0 / 分支对照 / 短 session 提前 resume 走祖先链 + connect 路径） | Battle 1-4 |

**F1 Status 维护规则**（与 D1 第 277-281 行规则同构）：

- F1 是 Stage 4 当前研发进度的唯一一跳入口；串行 agent 开工前必须先读本表。
- 每个 battle 完成时只更新对应行 `Status`，并在 [CHANGELOG.md](CHANGELOG.md) 记录已完成交付；不要把子任务流水账写进本表。
- `Status` 建议值：`todo` / `in-progress` / `done-local @ YYYY-MM-DD (tests passed, commit pending)` / `done @ YYYY-MM-DD (commit <sha> / PR #N)` / `blocked: <一句话原因>`。`done-local` 不具备跨机器 handoff 效力；可接续状态至少需要 commit，最好已 push。
- 若 `done` 后发现需要回滚，将对应 battle `Status` 回退到 `todo` 或 `in-progress` 并写明原因，同时在 [CHANGELOG.md](CHANGELOG.md) 增加修复条目引用 revert commit / PR。
- battle 边界、顺序或依赖变化时，才修改 F2 / F3 或回到 A / B / C 记录依据；不要为进度更新修改 [ARCHITECTURE.md](ARCHITECTURE.md)。

### F2：Stage 4 启动前已敲定决策摘要

> 本节是 H1-H5 + S3 的固化结论一跳入口；详细辩论历史仍在 A3 / A4 / A5 / A6 / B5 各自原段落。

- **A4-ii（task_table 边界）**：**扩展现有 `sessions` 表**承载 branch 派生字段——只增 2 列 `parent_session_id TEXT NULL` + `branch_role TEXT NULL`（取值 `main` / `branch` / NULL=root）；`task_id` 复用现有 `active_task_id`、`last_active_at` 复用现有 `updated_at`（[task_memory.py:425](../src/agent_os/agent/task_memory.py) 已维护）、`is_main` 反查 `tasks.current_main_session_id`，均不新增字段。`tasks` 表保持 5 字段不变（兑现 [ARCHITECTURE.md](ARCHITECTURE.md) §1.4 与 D2 承诺）。详见 A4 段实测落地理由。
- **A5（CTE 内 task 级编排函数边界）**：按命令拆平铺函数（`agent_os/cte/resume_task.py` / `branch_task.py`），不建 `TaskOrchestrator` 类；模块间走对外 API（`MA.fetch_task_final_state` / `SR.get_voice_pack` / `ER.start_resumed_session`）；ER 包 `start_resumed_session(prompt, session_meta)` 高层入口供 CTE 调。
- **B5.c（resume 命令分离）**：单命令 `/task resume` 自动判断 connect/fork（默认阈值 30 分钟距上次活动 / 80% token 占用），保留 `--force-fork` / `--force-connect` flag override；trace 显式记录决策路径与触发规则。
- **task_history 实现路径（H5）**：复用 `CompactSummary` v1 schema 不动（不引入 v2），`task_history` 走独立 SQLite 表（`task_id` / `session_id` / `compact_summary_id` / `is_main` / `created_at`），与 [ARCHITECTURE.md](ARCHITECTURE.md) §1.4 第 377 行 "task 只持引用列表" 对齐。Stage 4 仅在 schema 层预留扩展位，不主动构建快照序列（具体见 F4）。
- **voice_pack=None fallback（S3）**：resume 装配时若 `voice_pack` 为 None 则跳过该 inline 段，trace 标 `voice_pack_skipped=true`；与 [ARCHITECTURE.md](ARCHITECTURE.md) §3.5 stage 时空悖论原则一致（"还没做 voice pack 就要求测 voice"——Stage 4 装配同样不强制存在）。

### F3：执行节奏建议

- **Battle 1 是核心**：Battle 2 / 3 / 4 都直接或间接依赖 Battle 1 的 final_state 实时合成与 resume 主路径。
- **Battle 2 + 3 紧邻 PR**：CoW 强依赖 branch 字段位（`originating_session_id` / `parent_session_id`）启用；分两 PR 会出现 "字段在但无消费者" 的空窗。
- **Battle 4 可与 Battle 2 / 3 并行**：trace 集成只 hook resume 入口，不阻塞 branch 实现；可由不同人并行实现。
- **Battle 5 是 GC 收口**：必须在 Battle 1-4 全部 done 后跑 baseline，否则 trace 字段不全。

### F4：暂缓但不可遗忘的研发管理项

- **`task_history` 雏形**：Stage 4 不做（推到 Stage 6 与 `cross_run_lessons` 一起），Stage 4 task summary 仅 schema 层预留扩展位；实施时按 F2 H5 路径走（独立 SQLite 表，不动 `CompactSummary` schema_version）。
- **A3 cross-task artifact 召回**：Stage 4 不做，推到 Stage 5 真实 business_writing 时回答（与 A3 当前推荐一致）；权重公式 / `--include-other-tasks` flag / 风险提示文本均不在 Stage 4 范围。
- **A6 章节切分策略**：Stage 4 不做，推到 Stage 5 真实 1.5 万字 business_writing 时回答（与 A6 当前推荐一致）；Stage 4 仅复用 [ARCHITECTURE.md](ARCHITECTURE.md) §3.3 现有降级链 `full → digest+tail_n`。
- **PR template**：暂缓创建（参考 D4 第 302 行规则）；若 Stage 4 5 battle 中出现 ≥ 1 次 Reference Check / F1 Status / CHANGELOG 漏填再触发。
- **Stage 4 baseline trace 用例**：Battle 5 启动前最终敲定（草案：隔天 resume 命中 L0 / 分支对照 / 短 session 提前 resume 走祖先链 + connect 路径），不在 Stage 4 启动前预先固化。

---

**修订记录**：

- 2026-04-28：初版。从 7 轮 GPT 挑刺中提炼出 4 + 4 + 4 项开放问题；A 类等代码、B 类等用户、C 类等硬证据。
- 2026-04-28（同日补丁）：与 [ARCHITECTURE.md](ARCHITECTURE.md) 4 处自完备性补丁同步——
  - A1 微调（去掉已上提到总纲的"哪些字段必须非空"，留具体阈值 / 算法）。
  - A3 微调（chunk lazy 建立时机已上提到总纲，本条只剩权重公式 + 跨 task flag 决策）。
  - A5 新增（CTE 内 task 级编排函数 / 类边界，Stage 4 代码回答）。
  - B1 理由更新（`business_writing_pack` 整层 nullable 替代单字段 nullable）。
- 2026-04-28（同日 SOTA 同步）：与 [ARCHITECTURE.md](ARCHITECTURE.md) 12 处 SOTA 补丁同步——
  - **A2 扩展**：finalize 业务字段集（`final_at` / `final_session_id` / `final_by` / 单 vs 多 final）；命令名已上文档。
  - **A3 扩展**：剩余仅参数级（权重公式、风险提示文本措辞）；多级隔离规则与 prompt 标签机制已上文档。
  - **A6 新增**：超长 deliverable 章节切分策略（`/deliverable split` 命令 + 切分粒度 + digest_only 兜底字段集）；降级链 `digest+tail_n` 已上文档。
  - **A7 新增**：SR → CTE schema fragment 注册接口具体签名（机制已上文档）。
  - **A8 新增**：`pinned_refs` 填充 / 取消 pin 策略（字段位已上文档）。
  - **B5 新增**：断线重连 vs 跨天恢复命令分离决策（推荐 B5.c 单命令自动判断）。
- 2026-04-29：新增 **D 章节** Stage 2 Battle 排序（结论已定、待执行）——固化 6 个 battle 顺序与依赖、branch/CoW 推到 Stage 4 的依据；与 [ARCHITECTURE.md](ARCHITECTURE.md) 第 4 节 Stage 2 一句话承诺末尾的 D 引用配套落地。
- 2026-04-29（同日，Phase 7 收口：Stage 2/3 实测回填与出口规则执行）：
  - **A1 移除**：GC 字段级断言口径已固化到 [GC_SPEC.md](GC_SPEC.md)，并沉淀到 [ARCHITECTURE.md](ARCHITECTURE.md) §3.5；OPEN_DECISIONS 不再承载该条目。
  - **A7 收敛**：schema fragment 契约签名已在 Stage 3 落地并沉淀回 [ARCHITECTURE.md](ARCHITECTURE.md) §3.2；本节仅保留参数级残留（多 skill 合成 / 缓存 / size 阈值策略随 PR/GC trace 微调）。
  - **B3 移除**：Multi-Skill / Router 已完全归 [ARCHITECTURE.md](ARCHITECTURE.md) §3.6（默认不做，Stage 7+ 硬证据再开），OPEN_DECISIONS 不再作为待决策项承载。
  - **B4 关闭**：task 字段集边界已确认并沉淀到 [ARCHITECTURE.md](ARCHITECTURE.md) §1.4（不加 tag / parent_task_id / due_date）与 §3.6（participants 永远不做）；本条保留历史备查，不再作为待决策项反复讨论。
- 2026-04-29（同日，Phase 8 Stage 4 启动前决策收口）：
  - **F 章节新增**：固化 Stage 4 5 个 battle 顺序（`/task resume` v0 / `/task branch` v0 / Artifact CoW v0 / Resume Trace + `/context` 集成 / GC-Resume 收口）+ Status 维护规则（与 D1 同构）+ Stage 4 启动前已敲定决策摘要（A4-ii / A5 / B5.c / H5 task_history 路径 / S3 voice_pack=None fallback）+ 执行节奏建议 + F4 暂缓项（task_history 推 Stage 6、A3 / A6 推 Stage 5、PR template 暂缓、Battle 5 baseline trace 推 Battle 5 启动前）。
  - **A3 / A4 / A5 / A6 / B5 五条均追加 Stage 4 启动前确认 / 路径决策段**：固化 Stage 4 5 battle 实施时的方向，避免开工后再开放讨论。其中 **A4 直接采用 Phase 8 落地修正版**——经实测核查 [task_memory.py:138-146 / 425](../src/agent_os/agent/task_memory.py)，`sessions` 表已存在并已维护 `active_task_id` / `updated_at`，因此 Stage 4 仅扩展现有 `sessions` 表 +2 列（`parent_session_id` + `branch_role`），跳过中间 6 字段方案；`task_id` / `last_active_at` / `is_main` 全部复用现有字段不新增。
  - **[ARCHITECTURE.md](ARCHITECTURE.md) §4 Stage 4 一句话承诺末尾追加 F 引用**（与 §609 Stage 2 末尾 D 引用同构，属视图缺失补足型自完备性补丁）；ARCH 主干 0 修订。
  - **本次 Phase 8 不开始任何 Stage 4 代码实现**，只锁定文档层决策；Stage 4 Battle 1 由其他 coding agent 接手时按 F1 / F2 路径执行。
- 2026-04-29（同日，Phase 8 同日补丁：A8 Stage 4 边界明文）：A8 段追加 "Stage 4 启动前确认" 段，明文 "只消费不填充" 边界——Stage 4 Battle 1 `/task resume` v0 final_state 实时合成（F1 表）虽然显式引用 `pinned_refs`，但仅消费已存在字段（默认空列表→跳过 inline；非空→按 [ARCHITECTURE.md](ARCHITECTURE.md) §3.3 强制 inline），不实现 `/memory pin` / `/asset pin` / `/memory unpin` 命令；填充策略仍归 Stage 6（与 A8 当前 "回答方式" 一致）。与 S3 voice_pack=None fallback 同构，避免 coding agent 误判 Stage 4 需要先做 pin 命令链路。配套修改：[ARCHITECTURE.md](ARCHITECTURE.md) line 5 / line 7 元数据自完备性补足（加 Stage 4 (F) 引用 + GC_SPEC 已建当前时措辞）。**所有补丁主干 0 修订**——属视图缺失补足 + 读者跳转链路修正，A1-A8 / B1-B5 / C1-C4 / D / E / F 章节正文规则 0 改动。
