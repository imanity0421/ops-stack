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

### A1：GC 断言强度按 stage 分级的具体写法

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 3.5 已定原则——同一条 GC 在不同 stage 验证时断言条件不同；并已写出三层 schema 路径下的 Stage 3 / Stage 5 GC4 / GC5 断言矩阵。

**待回答**：

- 三层 schema 之外的字段级断言精确文本（如 `core.constraints` 中"含品牌红线"用关键词正则、子串、还是结构化字段）。
- 是否在代码层用 `gc_assertion_level: "stage3" | "stage5"` 参数驱动同一断言函数，还是各 stage 独立写一份断言文件。

**回答方式**：Stage 3 写第一条真实 GC（GC4-Stage3）时落地，同步建 `docs/GC_SPEC.md`；Stage 5 复用同函数追加字段时验证设计是否成立。

**当前推荐**：参数驱动单函数 + GC_SPEC.md 表格化字段集——避免"五份近似断言文件"的复制粘贴维护代价。

### A2：Deliverable Lifecycle 字段定义归 Stage 2 还是 Stage 5

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.3 已定——版本控制（current / previous / final）是池 2 的字段语义，归 MA；业务消费（如 `/skill deliverable promote`）归 SR。1.4 已预留 `/artifact finalize <id>` 命令名（Stage 5 实现）。

**待回答**：

- Stage 2 是否提前定义 `subkind: "draft" | "current" | "final"` 字段并接通 `/artifact` 命令的版本切换语义，还是 Stage 5 才接业务命令。
- 池 2 schema 中 `subkind` 是 enum 还是开放 string（开放允许 skill 自定义子分类）。
- `/artifact finalize` 业务字段：是否需要 `final_at` / `final_session_id` / `final_by` 等审计字段；finalize 是否阻止 update（强只读）还是仅作标签提示。
- 单 task 是否允许多 final（不同 deliverable 类型，如"方案 + 附件"）还是强制单一 final。

**回答方式**：Stage 2 PR 决定 subkind 字段是否预留；Stage 5 Battle 4 决定业务命令字段集与多 final 策略。

**当前推荐**：Stage 2 预留 subkind 开放 string + `previous_subkind_history` 列；Stage 5 接业务命令——`final_at` 必加、`final_session_id` 必加、`final_by` 可选；`finalize` 后 artifact 强制只读直到 `/artifact unfinalize`；单 task 默认允许多 final（用户场景常见"主方案 + 配套素材"）。

### A3：池 2 召回不物理隔离的具体权重 / 阈值

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.3 已定多级隔离规则——branch session 默认互不召回（仅当前 session 及祖先链）、跨 task 召回必带 prompt 装配层硬隔离标签（`<artifact ref task_id=... cross_task=true>` + 风险提示文本）、archived 默认排除。chunk lazy 建立时机也已上提到总纲。

**待回答（仅剩参数级）**：

- "权重略低"的具体公式（cross_task 系数 0.5？0.7？随 task 距离衰减？）。
- 是否需要 `--include-other-tasks` flag 显式开启跨 task 召回，还是默认召回时统一带回但 prompt 标签 + 权重降权。
- prompt 风险提示文本的精确措辞（中英？模板化？是否随 task 类型微调）。

**回答方式**：Stage 2 上线 artifact registry 后跑 5-10 个 business_writing 真实 case，看跨 task 命中是否真有用；Stage 4 resume 跑通后做 A/B 对比（含 / 不含风险提示文本对 LLM 数字锚定的抑制效果）。

**当前推荐**：默认仅当前 task；显式 flag 跨 task；权重系数 0.6 起步；archived 默认排除；风险提示文本英文模板优先（LLM 训练语料分布偏向英文指令）。

### A4：task_table 是否真的够 5 字段

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.4 已定 5 字段（task_id / name / status / created_at / current_main_session_id）。

**待回答**：

- 实际 Stage 2 跑代码后是否需要补字段（如 `last_active_at` 加速归档判断、`tag` 支持任务分类、`parent_task_id` 支持 task 树）。
- `current_main_session_id` 是否要扩展为 `session_history: list[str]` 以支持更精细的分支管理。

**回答方式**：Stage 2 PR 实现 task entity v0 后跑 1-2 周真实任务，看哪些字段是"必须的"哪些是"加上更舒服但其实可以查 session 表得到"。

**当前推荐**：5 字段先发；扩展字段进 [ARCHITECTURE.md](ARCHITECTURE.md) 1.4 时回填。`parent_task_id` 与 task 树相关，归 B4 决策。

### A5：CTE 内 task 级编排函数 / 类边界

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.1 跨模块编排归属规则已定——task 级编排归 CTE，表现层只调单个高层 API（如 `CTE.resume_task(task_id)`），由 CTE 内部 fan-out 到 MA / SR / ER。

**待回答**：

- CTE 内承担 task 级编排的代码组织：单个 `TaskOrchestrator` 类承担 resume / branch / compact 协调？还是按命令拆 `resume_task.py` / `branch_task.py` / `compact_task.py` 平铺函数？
- 编排函数对其他模块的调用是否走"模块对外 API（如 `MA.fetch_task_final_state`）"还是直接 import 子模块函数。前者更可测试，后者更直接。
- CTE → ER 启动新 session 的入口（`ER.run_session(...)` 还是直接调 agno）。

**回答方式**：Stage 4 写 `/task resume` 时落地——这是第一个真实跨 4 模块编排的命令。Stage 5 写 `/skill compose context_pack` 时再验证 SR 级编排是否结构同构。

**当前推荐**：先按命令拆平铺函数（避免过早抽象 Orchestrator 类）；模块间走对外 API 不直接 import 子函数；ER 包一个 `start_resumed_session(prompt, session_meta)` 高层入口供 CTE 调。

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

### A7：SR → CTE Schema Fragment 注册接口具体签名

**已知**：[ARCHITECTURE.md](ARCHITECTURE.md) 3.2 已定机制——SR 通过 schema fragment 注册接口向 CTE 暴露 business_writing_pack / skill_state 的 Pydantic / JSON Schema；CTE 在 compact LLM 调用前合成完整 schema。机制级已上文档，本条只剩接口签名级。

**待回答**：

- 注册时机：启动时一次性注册，还是 compact 调用前动态注册？
- 接口签名：`Protocol.get_compact_schema_fragment(self) -> Type[BaseModel]` 是否够，还是需要返回 `(Pydantic Type, schema_metadata)` 二元组以支持 schema 版本管理？
- 多 skill 同时活跃时如何合成（concat fields？嵌套字典？）——当前架构 single-skill-active 假设下不存在该场景，但接口应预留扩展位。
- schema fragment 缓存策略——LLM API 提交 schema 较大、缓存可降首字延迟。

**回答方式**：Stage 3 写第一个 compact 实现时落地；Stage 5 引入 business_writing skill 时验证接口在跨 skill 类间的复用性。

**当前推荐**：启动时注册（让 DI 容器主导）；签名为 `Protocol.get_compact_schema_fragment() -> Type[BaseModel]`，schema 版本管理交给 Pydantic model 内部 `schema_version` 字段；多 skill 暂不支持（第一个实现强制 single-skill-active）。

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

### B3：是否做 Multi-Skill Composition / Skill Router

**背景**：原 V1 设想 skill 多到一定程度后引入 router 自动路由。

**当前选择**：**默认不做（推到 Stage 7+），除非届时出现硬证据再开**——与 [ARCHITECTURE.md](ARCHITECTURE.md) §3.6 反模式清单口径一致。

**理由**：

- 个人级用户同时活跃的 skill 数 ≤ 3-5 个，路由价值极低。
- Skill Router 一旦引入就要解决"路由错怎么办、用户能否覆盖、组合时上下文如何拼"——全是平台化问题。
- 用户显式 `/skill switch` 比自动路由更可控、更不易出错。

**反向触发条件（硬证据）**：用户长期同时管理 ≥ 8 个 skill 且抱怨切换成本时再考虑。当前看不到该场景。

### B4：Task 实体的产品语义边界

**背景**：[ARCHITECTURE.md](ARCHITECTURE.md) 1.4 task_table 极简 5 字段。但 task 实体可以扩展的语义维度很多。

**待决策（用户拍）**：

- B4.a：是否需要 `tag` 字段（任务分类，"商业 / 运营 / 写作"）。
- B4.b：是否需要 `parent_task_id`（task 树支持子任务）。当前推荐**不做**——会引入治理复杂度。
- B4.c：是否需要 `due_date`（用户驱动的 deadline）。当前推荐**不做**——agent 自己不催办。
- B4.d：是否需要 `participants`（多人协作）。**永远不做**——超出个人级范围。

**当前推荐**：B4.a 可加（轻量、用户主动填），其余全部不做。

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
| 6 | **Trace + `/artifact` + `/context` Integration** | todo | trace 字段持续记录 artifact 引用、`/context` 显示 artifact 占比、artifact 召回作 trace 归因 | Battle 5 |

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
- **GC_SPEC.md**：暂缓创建 `docs/GC_SPEC.md` 空骨架。第一条真正 Golden Case 落地时创建；预计最晚在 Battle 6（Trace + `/artifact` + `/context` Integration）前补齐，承载字段级断言与 Stage 断言强度矩阵。
- **Stage 2 baseline trace**：暂缓执行 baseline capture。进入 Battle 6 收口前，补跑 1-2 个真实长任务用例，记录 Stage 1/Stage 2 对照的 context 长度、预算占用、artifact/tool result 相关 trace；记录位置优先放入 `docs/GC_SPEC.md` 或测试 fixture / benchmark 记录，不写入 [CHANGELOG.md](CHANGELOG.md)。

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
