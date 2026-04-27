# Context Management V2 接手版

本文是 `agent-os-runtime` 的上下文工程专项接手手册。Memory V2 解决“记忆如何分层、写入、召回、治理”；Context Management V2 解决“每轮 Agent run 如何把静态规则、外部召回、工作记忆、recent history 与当前用户目标送进模型上下文”。

全局业务架构方向、Claude Code Harness 参考边界和 Stage 节奏以 [CLAUDE_CODE_REFERENCE_ROADMAP.md](CLAUDE_CODE_REFERENCE_ROADMAP.md) 为中心；本文只负责 Context 子系统的已落地语义、接手信息、验证口径和历史追溯，不承担全局路线图职责。

本文件现在采用“主文 + 附录”结构：主文只保留新 Agent 接手必要信息；历史 P1 / P2 / P2-H 逐项说明保留在附录，避免丢失追溯能力。

## 当前状态快照

当前项目已经形成 Context Management V2 可验收版本：

- `ContextBuilder` 统一构造每轮动态上下文：`runtime_context`、`external_recall`、`working_memory`、`recent_history`、`attention_anchor` 与最终 `current_user_message`。
- `retrieve_ordered_context` 输出 XML-like evidence bundle，按 Mem0 -> Hindsight -> Graphiti -> Asset Store 组织外部召回，并具备 `usage_rule`、abstain、query planning、squeezing / JIT hint 与 trace。
- 静态 `instructions` 已收敛到 constitutional rules、skill contract、manifest system prompt 与工具 schema；ephemeral metadata、TaskMemory、retrieved context、recent history、当前锚点不再混入静态前缀。
- Context V2.6 已完成 P2-H19 ~ P2-H25：Web 演示提示下沉到 `attention_anchor`、自动召回与工具重复调用短路、runtime context 时间去秒、`restated_goal`、短 tool output 保留原文、`last_deliverable` 兜底、manifest miss 一次性 INFO。

最新验证口径：

- 上下文管理矩阵通过：`python -m pytest tests/core/test_context_builder.py tests/core/test_p2_boundary_negative.py tests/core/test_ordered_context.py tests/core/test_web_admin_api.py tests/core/test_cli.py tests/core/test_session_persistence.py tests/core/test_p1_constitutional_output.py -q`。
- 相关文件 `ruff check` 与 `ReadLints` 通过。
- 全量 `python -m pytest tests/core -q` 仍可能受非上下文管理用例 `test_ingest_asset_store_minimal_with_allow_llm_off` 的真实 OpenAI embedding 网络依赖阻塞；这不是 ContextBuilder / ordered context 回归。

## 接手必读：四层架构

每轮模型上下文按四层组织：

1. 顶部绝对静止层：system rules、tool definitions、global guidelines、skill contract。
2. 外部召回层：Mem0 profile、Hindsight lessons、Graphiti / fallback domain knowledge、Asset Store style reference / source material。
3. 动态工作记忆层：TaskSummary、TaskIndex、recent history、`last_deliverable` fallback；SubAgent / Todo / Plan 通道仍属 V3。
4. 注意力锚定层：current user request、`restated_goal`、extracted constraints、tool boundary、entrypoint notice、success criteria；最终完整用户原文仍在 `current_user_message`。

优先级定义：

- P0：主要使用路径不可用、上下文严重失控、成本或质量在常规使用中快速恶化。
- P1：稳定降低模型判断、召回可信度、工具选择或上下文可控性，应进入 Context V2 主线。
- P2：1 / 2 / 4 层 SOTA 增强与第 3 层简单迭代。
- P3：复杂工作记忆，例如 SubAgent、Rewind、自动 task boundary、长任务治理。
- P4+：平台化、鉴权、多租户、计费、审计、严格 XML schema / parser、复杂并发治理。

## 代码全貌索引

| 路径 | 职责 |
| --- | --- |
| `src/agent_os/context_builder.py` | 每轮动态上下文入口；`ContextBuilder.build_turn_message`、auto retrieve decision、history clean、attention anchor、V2.6 的 contextvar / `restated_goal` / `entrypoint_notice` / `last_deliverable` |
| `src/agent_os/runtime_context.py` | 构造 ephemeral runtime context；当前时间为分钟级，避免秒级 prompt cache 抖动 |
| `src/agent_os/memory/ordered_context.py` | 渲染 XML-like ordered context，组织 Mem0 / Hindsight / Graphiti / Asset Store evidence |
| `src/agent_os/memory/controller.py` | `MemoryController.retrieve_ordered_context` 对外召回入口 |
| `src/agent_os/agent/factory.py` | `get_agent` 组装静态 instructions、tools、session DB 与 manifest；manifest miss 一次性 INFO |
| `src/agent_os/agent/tools.py` | Agent tools；`retrieve_ordered_context` 工具在自动召回命中轮次返回 stub，避免重复召回 |
| `src/agent_os/cli.py` | CLI 入口：读取用户输入、可选自动召回 / TaskMemory、构造 run message、调用 `agent.run` |
| `examples/web_chat_fastapi.py` | Web demo；`/chat` 入口通过 `entrypoint_extra_lines` 把 Web 演示提示放入 `attention_anchor` |
| `src/agent_os/config.py` | ContextBuilder、auto retrieve、budget、TaskMemory、history 等开关 |
| `tests/core/test_context_builder.py` 等 | Context V2 / V2.6 的主要回归矩阵 |

## 每轮数据流

CLI / Web 的 ContextBuilder 路径一致：

1. 入口读取用户输入。
2. `get_agent` 创建 Agno Agent，静态 instructions 只保留稳定规则与 skill contract。
3. 可选读取 TaskSummary / TaskIndex。
4. 可选 `resolve_auto_retrieve_decision`，命中时调用 `build_auto_retrieval_context`，结果进入 `external_recall`。
5. 从 Agno session DB 或本地 transcript 取 history，交给 `clean_history_messages` 清洗。
6. `ContextBuilder.build_turn_message(...)` 输出 `ContextBundle.message` 与 `ContextTrace`。
7. `agent.run(run_message, session_id=..., user_id=...)` 执行本轮。

Web 特例：启用 ContextBuilder 时，`_WEB_EXTRA_INSTRUCTIONS` 不进入静态 `extra_instructions`，只在 `/chat` 调用 `build_turn_message` 时通过 `entrypoint_extra_lines` 进入 `<attention_anchor><entrypoint_notice>`；ContextBuilder 关闭的 legacy 模式仍保留静态兼容路径。

## 防循环备注与已定语义

| 事项 | 当前状态 | 接手时应做什么 | 不应重复做什么 |
| --- | --- | --- | --- |
| 四层上下文主线（P1-1 ~ P1-6） | 已完成，可验收 | 仅在发现真实回归时补具体测试 | 不要重新讨论是否需要 ContextBuilder、是否要把 runtime / recall / working memory 放回静态 instructions |
| 原始 P2-1 ~ P2-13 | 已完成自用第一版 | 真实使用暴露质量问题时新增 P2-H 小项 | 不要把复杂 LLM planner、严格 XML schema、平台 trace sink 当成未完成 P2 继续循环 |
| P2-H1 ~ P2-H10 | 已完成 hardening | 新异常输入或 Agno message 形态变化时补 fixture | 不要重新打开 XML-like 边界、动态块顺序、双 history 配置、Web/CLI Asset 对齐等整体方案 |
| Context V2.4 / P2-H11 ~ P2-H13 | 已完成近期收敛线 | 只接受明确回归或新 P2-H bugfix | 不要重复修 attention anchor 全文重复、Web TaskMemory parity、handoff 时间字段、`_shorten` 极小预算、runtime context 标识符转义、CLI history cap |
| Context V2.5 / P2-H14 ~ P2-H18 | 已完成独立评审收敛线 | 只接受明确回归或新 P2-H bugfix | 不要重复修英文大小写 / 子串误匹配、零宽关键词、auto retrieve 关闭态 trace、handbook version 静态层、空 ordered context shell、Graphiti 空白返回、runtime context `None`、adapter 异常软降级 |
| Context V2.6 / P2-H19 ~ P2-H25 | 已完成上下文工程独立评审第二轮 | 若引入新 entrypoint，按“演示 / 边界提示走 attention_anchor / manifest，不塞静态 instructions”补回归 fixture | 不要重开 Web 演示提示静态层、runtime 秒级时间、`restated_goal`、自动召回工具短路、tool output 一刀切折叠、`last_deliverable` 兜底、manifest miss warn |
| P3 / V3 复杂工作记忆线 | 只记录，不在当前轮实现 | 等用户明确升级或真实任务证明必要后再规划 | 不要主动实现 SubAgent 隔离、Rewind、自动 task boundary、多模态历史治理 |
| P4+ 平台化能力 | 冻结到条件触发 | 仅在外部系统、生产多租户或机器解析需求出现后启动 | 不要在自用阶段提前做鉴权平台、计费、严格 XML parser、学习型 auto retrieve / rerank |

已定语义：

- `hard_total_budget` 不裁剪最终 `current_user_message`；超预算只通过 `context_budget over_budget` 与 `current_message_high_ratio` 观测。
- `attention_anchor` 可摘要化当前目标，但最终 `current_user_message` 保留完整用户原文。
- `mode=always` 遇到空白用户消息仍返回 `empty`，不触发自动召回。
- 空 query 的 relevance gate 不因 overlap 规则拒绝全部结果。
- 显式开启 `AGENT_OS_CONTEXT_ALLOW_AGNO_HISTORY_WITH_BUILDER=1` 是逃生配置，会使用 Agno 原始 `session_history_max_messages`，不套 ContextBuilder 的 TaskSummary cap。
- P2-H20 的自动召回互斥是工具层 stub 短路，不是从 Agent schema 中物理删除 `retrieve_ordered_context`。

## 当前完成矩阵

| 范围 | 状态 | 关键代码 / 测试 | 备注 |
| --- | --- | --- | --- |
| P0 | 无新增项 | 附录 A | 暂无需要按生产事故处理的上下文问题 |
| P1-1 ~ P1-6 | 已完成 | `context_builder.py`、`factory.py`、`tools.py`、`test_context_builder.py`、`test_p1_constitutional_output.py` | 四层主线、history 清洗、召回不是指令、attention anchor、默认工具收窄 |
| P2-1 ~ P2-13 | 已完成自用第一版 | `ordered_context.py`、`context_builder.py`、`test_ordered_context.py` | XML-like evidence、abstain、Query Planning、squeezing、JIT、budget、trace |
| P2-H1 ~ P2-H10 | 已完成 hardening | `test_p2_boundary_negative.py` 等 | Web history、预算裁切、prompt 边界、动态块顺序、双 history 配置、异常输入鲁棒性 |
| Context V2.4 / P2-H11 ~ P2-H13 | 已完成 | `context_builder.py`、`factory.py`、`cli.py` | attention anchor squeezing、static prefix / KV cache hygiene、Web TaskMemory parity、边界补丁 |
| Context V2.5 / P2-H14 ~ P2-H18 | 已完成 | `context_builder.py`、`ordered_context.py`、`web_chat_fastapi.py` | residual dynamics、anchor 语义抽取、assistant 长交付物保留、auto/manual 去重提示、Web recall client reuse |
| Context V2.6 / P2-H19 ~ P2-H25 | 已完成 | `context_builder.py`、`runtime_context.py`、`factory.py`、`tools.py`、`web_chat_fastapi.py` | `entrypoint_notice`、auto retrieve stub、分钟级 runtime、`restated_goal`、短 tool output 保留、`last_deliverable`、manifest miss INFO |

## V3 / P4+ 条件触发清单

这些事项不是当前未完成 bug：

- SubAgent 结果、Todo、Plan、中间产物、artifact reference 独立通道。
- 自动 task boundary、Rewind、复杂长任务工作记忆治理。
- 自动召回 per-layer timeout、真正并行、partial evidence 与软降级。
- LLM rerank、学习型 auto retrieve、自适应 budget。
- 严格 XML schema / parser、统一 sanitizer、标签白名单平台。
- Web 多 worker / 多实例 session DB 并发治理。
- 生产鉴权、多租户、计费、审计与平台化 prompt-injection 防护。

## 附录说明

下面保留历史问题清单、推荐路线与自审。它们用于追溯“为什么这样设计”，不是当前待办列表。接手时先读主文；只有排查真实回归或需要理解历史取舍时再读附录。

## 附录 A：历史问题清单与逐项说明

### P0

当前没有发现必须按 P0 处理的新增上下文问题。

说明：之前讨论中的“缓存击穿”“隐性智障”属于真实风险，但在当前代码规模和自用场景下，更适合定为 P1。它们应优先修，但不必按生产事故处理。

### P1：本轮必须处理

#### 1. 顶部静止层混入动态信息

解决状态：**已完成**。

问题提出时，`get_agent` 把 constitutional prompt、ephemeral metadata、task summary、task index、manifest prompt、handoff、extra instructions 全部放入 Agno `instructions`。

风险：

- 当前时间等每轮变化信息进入高位 instructions，可能降低 prompt cache 稳定性。
- task summary / task index 与真正静态规则混在一起，模型会把动态工作记忆看得过重。
- 随着 ContextBuilder 引入，静态层与动态层如果继续复用同一通道，会让优先级变得模糊。

建议：

- 把 `instructions` 收敛为尽量稳定的系统规则与 skill contract。
- 将运行时元数据、task summary、retrieved context、current anchor 放入每轮 ContextBuilder 输出，而不是混入静态 instructions。
- 如果 Agno 限制导致仍需放入 instructions，则至少拆成明确 XML/section，并尽量移除秒级时间。

相关位置：

- `src/agent_os/agent/factory.py`
- `src/agent_os/runtime_context.py`
- `src/agent_os/agent/task_memory.py`

已落地：

- `get_agent` 中的静态 instructions 已收敛为 constitutional blocks、skill contract、manifest prompt、handoff 与必要 extra instructions。
- `ephemeral metadata`、TaskSummary、TaskIndex、external recall、recent history 与 current anchor 由 `ContextBuilder.build_turn_message` 在每轮动态构造。
- `enable_context_builder` 与 `context_self_managed_history` 打开时，`get_agent` 不再让 Agno 自动注入原始 history，避免静态层与动态层混线。

验证：

- `tests/core/test_context_builder.py` 覆盖 runtime context、working memory、attention anchor 与 current message 的结构。
- `tests/core/test_p1_constitutional_output.py` 覆盖静态宪法 / skill 输出边界。
- 全量 `pytest` 与 Ruff 已通过。

#### 2. `retrieve_ordered_context` 大块工具输出可能回灌到历史

解决状态：**已完成**。

问题提出时，四层召回结果一旦作为工具输出进入 Agno run，后续 session history 可能再次带入同一大块内容。

风险：

- 后续轮次即使用户目标变化，旧召回结果仍可能占据注意力。
- token 成本和延迟增加。
- 低相关或过期记忆被历史回灌放大。

建议：

- 优先验证 Agno history 是否包含工具调用结果，并实现 history clean interceptor 或自管 history 注入的兜底路径。
- 若可控，应默认不把大块检索工具输出写入可回灌历史。
- 若不可控，应让 `retrieve_ordered_context` 返回短摘要或 evidence bundle，而不是完整 Markdown 块。
- 将 `AGENT_OS_SESSION_HISTORY_MAX_MESSAGES` 与检索输出策略联动；检索较重时减少 history 回灌。

相关位置：

- `src/agent_os/agent/factory.py`
- `src/agent_os/agent/tools.py`
- `src/agent_os/memory/ordered_context.py`
- `src/agent_os/observability.py`

已落地：

- `clean_history_messages` 会折叠 `role=tool` 的输出，只保留短摘要和“工具结果已折叠”标记。
- 对包装过的上一轮用户消息，会从 `<current_user_message>` 中解包真实用户输入，避免 ContextBuilder 生成的整包上下文被下一轮当作历史回灌。
- CLI 路径通过 `_session_messages_for_context()` 从 Agno DB 读取历史后交给 ContextBuilder 清洗。
- Web 路径已在二次审查后改为：启用 ContextBuilder 时优先从 Agno 持久化 session DB 读取历史，失败或未挂库时才回退 `_transcripts`，避免进程重启后“前端展示历史但模型没历史”。

验证：

- `tests/core/test_context_builder.py` 覆盖 tool output replay 折叠、wrapped user message 解包。
- `tests/core/test_web_admin_api.py` 覆盖 Web ContextBuilder 优先读取持久化 session history 与内存 fallback。
- `tests/core/test_cli.py` 覆盖 CLI 交互路径的空白输入跳过与异常字符输入。

#### 3. 外部召回不是每轮 ContextBuilder 的确定性阶段

解决状态：**已完成**。

问题提出时，Memory V2 召回主要通过 `retrieve_ordered_context` 工具暴露，模型需要主动调用。manifest 只是提示“可按需调用”。

风险：

- 模型未调用工具时，本轮没有 Memory V2 外部召回。
- 同类任务表现不稳定，有时像“有记忆”，有时像“没记忆”。
- 无法统一做预算、格式化、防注入、trace。

建议：

- 增加显式 ContextBuilder 路径：根据任务类型决定是否预取召回。
- 保留工具按需召回作为补充，但不要让它成为唯一外部召回入口。
- 至少为策略类、方案类、交付类任务提供自动预取开关，例如 `AGENT_OS_CONTEXT_AUTO_RETRIEVE=1`。

相关位置：

- `src/agent_os/agent/tools.py`
- `src/agent_os/memory/ordered_context.py`
- `src/agent_os/cli.py`
- `examples/web_chat_fastapi.py`

已落地：

- 新增 `build_auto_retrieval_context`，在 CLI / Web 进入 `ContextBuilder` 前可按策略自动预取 `retrieve_ordered_context`。
- `resolve_auto_retrieve_decision` 支持 `keywords`、`always`、`manual`、`off` 等模式，并返回可观测 reason。
- `Settings` 与 skill manifest 都可覆盖自动预取模式和关键词。
- 手动工具 `retrieve_ordered_context` 仍保留，作为模型按需补充召回入口。

验证：

- `tests/core/test_context_builder.py` 与 `tests/core/test_p2_boundary_negative.py` 覆盖自动预取模式、非法 mode、空关键词、空白用户消息等边界。
- CLI / Web 接入已在定向测试与全量测试中通过。

#### 4. 召回内容缺少“数据不是指令”的防注入边界

解决状态：**已完成**。

问题提出时，系统宪法定义了冲突解决序，但没有足够明确地要求模型把 Memory / Graphiti / Asset 返回内容视为 evidence/context，而不是指令。

风险：

- 外部素材或历史经验中若包含命令式语句，可能影响模型行为。
- Asset Store 的案例、Graphiti fallback 文本、Hindsight 教训可能被模型误当成高优先级规则。
- 未来导入长素材后，prompt injection 风险会增加。

建议：

- 在顶部静态层加入规则：召回结果是数据，不得覆盖 system / developer / current user 指令。
- 在召回输出中为每块标注 `usage_rule`，例如 `evidence_only`、`style_only`、`background_only`、`lesson_only`。
- 对 Asset source material 与 style reference 分别声明使用边界。

相关位置：

- `src/agent_os/agent/constitutional.py`
- `src/agent_os/memory/ordered_context.py`
- `src/agent_os/memory/context_formatters.py`
- `src/agent_os/knowledge/asset_store.py`

已落地：

- 静态宪法规则与 `external_recall` 都明确声明外部召回是 evidence / context，不得覆盖 system、developer 与当前用户指令。
- `retrieve_ordered_context` 的 Mem0、Hindsight、Graphiti、Asset 块都带 `usage_rule`、`authority`、`source`、`relevance` 等边界信息。
- Asset Store 拆为 `style_reference` 与 `source_material`，分别约束为 `style_only` 与 `source_material_only`。

验证：

- `tests/core/test_context_builder.py` 覆盖 `external_recall` 的 `evidence_only` 包装。
- `tests/core/test_ordered_context.py` 覆盖 Graphiti injection 文本仅作为 background evidence、Asset style/source 分离、usage rule 存在。

#### 5. 注意力锚定层缺失

解决状态：**已完成**。

问题提出时，`agent.run(message)` 直接传入用户消息，没有构造独立的 current task anchor。

风险：

- 历史、记忆、案例、工具输出可能压过当轮用户意图。
- 用户本轮格式、范围、目标变化时，模型需要自行从自然语言中抽取优先级。
- 长会话下容易出现“继续沿用旧目标”的惯性。

建议：

- ContextBuilder 最后一层增加 `<current_request>` 或等价结构。
- 明确本轮目标、硬约束、输出格式、下一步动作。
- 将“当轮用户显式指令高于历史召回”在 anchor 中重复一次。

相关位置：

- `src/agent_os/cli.py`
- `examples/web_chat_fastapi.py`
- `src/agent_os/agent/factory.py`

已落地：

- `ContextBuilder` 每轮生成 `<attention_anchor>`，其中包含 `<current_user_request>`、目标优先级、必须遵守本轮要求和 success criteria。
- 最终用户输入仍放在 `<current_user_message>`，位于整包 Context Management 消息之后，确保当前请求在结构上最后出现。
- `attention_anchor` 与最终 `current_user_message` 不参与普通块级裁切；硬预算也不会裁剪当前用户消息。

验证：

- `tests/core/test_context_builder.py` 覆盖 attention anchor 和当前请求保留。
- `tests/core/test_p2_boundary_negative.py` 覆盖极紧硬预算下超长当前用户消息仍完整保留。

#### 6. 默认工具暴露过宽

解决状态：**已完成**。

问题提出时，`enabled_tools=[]` 等价于“不筛选”，因此默认 skill 会挂载全部平台工具。工具数量继续增加时，顶部 tool schema 会变重，也会增加误调用概率。

风险：

- 工具定义常驻顶部，影响 token 与 prompt cache。
- 模型工具选择负担增大。
- skill 越多，默认工具集越不稳定。

建议：

- 改为最小默认工具集，skill manifest 显式声明启用工具。
- 区分 platform tools、memory read tools、memory write tools、debug tools、skill tools。
- 自用场景不需要完整 Supervisor，但需要基础 Tool Masking。

相关位置：

- `src/agent_os/manifest_loader.py`
- `src/agent_os/agent/tools.py`
- `src/agent_os/data/skill_manifests/default_agent.json`

已落地：

- skill manifest 的 `enabled_tools` 语义已收窄：`null` 表示不筛选，`[]` 表示不暴露工具，非空列表表示只暴露白名单工具。
- 默认 skill manifest 已显式声明工具集合，debug / hindsight / asset 等工具可按配置开关暴露。
- Web 示例额外排除写记忆工具，避免模型在 Web 演示里假装已写入记忆。

验证：

- `tests/core/test_manifest_loader.py` 覆盖 manifest 工具白名单语义。
- `tests/core/test_build_memory_tools_flags.py` 覆盖工具开关与排除逻辑。

### P2：SOTA 增强与第 3 层简单迭代

P2 统一收纳两类事项：

- 1/2/4 层的 SOTA 增强：格式、预算、JIT、squeezing、相关性门控等。
- 第 3 层的简单迭代：history/summary 分工、入口一致性、基础 trace 等。

以下按**研发难度由低到高**排序。

当前实现状态（截至本文件最近一次同步）：

- **原始 P2-1 ~ P2-13 均已有可验收实现**，并通过全量 `ruff check .`、`ruff format --check .` 与 `pytest`。
- 当前完成口径是“自用阶段可落地版”：以确定性规则、字符预算、轻量 trace、可选 synthesis / JIT hint 为主；不等同于完整 LLM rerank、tokenizer 精算、平台级成本治理或复杂工作记忆系统。
- 后续新增工作中，仍属于 1/2/4 层质量、预算、trace、评测增强的事项，明确列入 **P2-H：V2.x Hardening**；涉及复杂工作记忆、SubAgent、Rewind、Web TaskMemory 对齐的事项列入 **P3**。
- P2 与路线的对应关系：P2-1 属于 V2.0 的工具噪声治理；P2-2 ~ P2-5 属于 V2.1；P2-6、P2-11 ~ P2-13 属于 V2.2；P2-7 ~ P2-10 横跨 V2.0 / V2.3，其中 P2-7 trace、P2-8 summary/history、P2-9 入口一致性先支撑 V2.0，再服务 V2.3 的可解释预算。

P2 单项解决总表：

| 项目 | 解决状态 | 已落地位置 / 说明 | 验证 |
| --- | --- | --- | --- |
| P2-1 工具描述压缩 | **已完成** | `agent.tools` 中描述已收敛；manifest 可按 skill mask 工具；Web 排除写记忆工具 | `test_build_memory_tools_flags.py`、`test_manifest_loader.py` |
| P2-2 XML-like 证据包 | **已完成** | `memory/ordered_context.py` 输出 `<ordered_context version="2.2">` 与分层 evidence | `test_ordered_context.py` |
| P2-3 Hindsight superseded 治理 | **已完成** | 默认不注入 superseded 旧经验；debug 分支可显示旧行和 score | `test_ordered_context.py`、`test_web_admin_api.py` 的 debug search |
| P2-4 Graphiti legacy fallback 权威标记 | **已完成** | Graphiti / fallback 输出带 `legacy_compat`、`fallback_knowledge` 等低权威标记 | `test_ordered_context.py` |
| P2-5 Asset style/source 拆分 | **已完成** | Asset 分别检索 / 输出 `<style_references>` 与 `<source_materials>` | `test_ordered_context.py` |
| P2-6 低相关召回拒绝注入门 | **已完成** | `memory/relevance_gate.py` 接入 Mem0 / Hindsight / Graphiti / Asset abstain | `test_ordered_context.py`、`test_p2_boundary_negative.py` |
| P2-7 基础 Context Trace | **已完成** | `ContextTrace`、`AGENT_OS_CONTEXT_TRACE`、`log_context_management_trace` | `test_context_builder.py`、`test_observability.py` |
| P2-8 Session history 与 Task summary 分工 | **已完成** | TaskSummary 存在时收紧 history cap；ContextBuilder 统一清洗 history | `test_context_builder.py`、`test_cli.py` |
| P2-9 CLI / Web 入口一致性 | **已完成** | CLI 与 Web 均接入 ContextBuilder；Web 二次修复为优先读持久化 session history | `test_cli.py`、`test_web_admin_api.py` |
| P2-10 统一 token / char 预算器 | **已完成字符级第一版** | `ContextCharBudget`、块级截断、硬预算可选、token estimate trace | `test_context_builder.py`、`test_p2_boundary_negative.py` |
| P2-11 Query Planning | **已完成确定性第一版** | `memory/query_plan.py` 生成分层 query，ordered context 可输出 `<query_plan>` | `test_ordered_context.py`、`test_p2_boundary_negative.py` |
| P2-12 Contextual Squeezing | **已完成最小版** | Hindsight / Asset synthesis 开关、Asset raw 节选上限、预算截断；Graphiti squeezing 保留为条件触发项 | `test_ordered_context.py`、`test_boundary_safety.py` |
| P2-13 JIT 按需加载 | **已完成设计预留** | evidence 中保留来源 / hint，不默认展开长原文；需要时通过工具再查 | `test_ordered_context.py` |

协作注意：上表中“最小版 / 第一版 / 设计预留”代表**当前 P2 已收口**，不是未完成 blocker。若后续真实使用暴露质量问题，应新增 P2-H / P3 条目，而不是把 P2-1~P2-13 重新打开循环修复。

#### P2-1 工具描述压缩

工具 description 适合开发期，但会常驻工具定义。压缩工具描述的研发难度最低，能直接降低顶部 tools definition 噪声。

建议：

- 稳定后压缩工具 description。
- 把复杂写入规则移到静态 policy 或工具内部返回错误，而不是全部写在 tool schema。
- 保留 debug / dev 模式下的长描述能力。

#### P2-2 召回输出升级为 XML/XML-like 证据包

当前输出主要是 Markdown 标题与分隔线，适合人读，但对模型来说边界不够硬。

建议：

- 改为 XML 或 XML-like 结构。
- 每条召回内容带 `source`、`scope`、`timestamp`、`authority`、`usage_rule`、`relevance`。
- 对用户可见回复仍可用 Markdown，但喂给模型的上下文应结构化。

#### P2-3 Hindsight superseded 注入治理

Memory V2.2 设计为 append-only，`supersedes` 是降权而非隐藏。这是正确的存储语义，但上下文注入层应更保守。

建议：

- 默认上下文只注入非 superseded 或高置信新版经验。
- debug 或诊断模式才显示 superseded 旧经验。
- 若旧经验仍被注入，应显式标记 `superseded=true` 与采用条件。

#### P2-4 Graphiti legacy fallback 权威标记

Graphiti 新语义是系统级干净知识，但仍兼容 legacy client-skill group。

自用阶段这不是核心问题，但实现难度不高，适合放在 P2 前段补齐。

建议：

- 在上下文输出中将 legacy fallback 标记为 `legacy_compat`。
- legacy 权威低于系统 Graphiti。
- ContextBuilder 可配置是否允许 legacy 进入主 prompt。

#### P2-5 Asset Store 拆分 style_reference 与 source_material

`retrieve_ordered_context` 里 Asset 检索使用 `asset_type=None`，最终统一进入“参考案例”。这是 1/2/4 层的 SOTA 增强，不是生存线 bug。

风险：

- `style_reference` 可能被误当作事实来源。
- `source_material` 可能被误当作风格范例。
- Few-shot 与 background 的边界不够硬。

建议：

- 在 ordered context 中分别检索 `style_reference` 与 `source_material`。
- 输出为独立块：`<style_references>` 与 `<source_materials>`。
- 风格块只允许借鉴结构、语气、节奏；素材块只允许抽取事实、故事、实体和细节。

相关位置：

- `src/agent_os/memory/ordered_context.py`
- `src/agent_os/knowledge/asset_store.py`
- `src/agent_os/knowledge/asset_synthesizer.py`

#### P2-6 低相关召回拒绝注入门

Hindsight 有评分与预算，其他层缺少统一 abstain 规则。

建议：

- 每层返回“是否值得注入”。
- 弱相关结果进入 trace，不进入 prompt。
- 对 Graphiti fallback、Asset system fallback 特别需要阈值，避免冷启动素材误导。

#### P2-7 基础 Context Trace

当前 observability 记录工具名与 token 粗算，但无法还原本轮上下文由哪些块组成。

建议：

- ContextBuilder 输出 trace：块名、来源、字符数、是否注入、是否压缩、截断原因。
- trace 默认不进 prompt，只进日志或 debug endpoint。
- 先做字符数与块级开关记录，不必一开始接 tokenizer。

#### P2-8 Session history 与 Task summary 分工

Agno session history 默认注入最近 N 条消息；TaskSummary 又可能基于同一批消息生成摘要。两者同时存在时，旧约束、旧目标和旧决定可能重复出现。

自用阶段这通常只是 token 浪费和轻微注意力干扰，因此属于第 3 层简单迭代。

建议：

- 明确 history 与 summary 的分工：summary 覆盖较早上下文，history 只保留最近原文。
- 当 summary 存在时，可减少 `AGENT_OS_SESSION_HISTORY_MAX_MESSAGES`。
- ContextBuilder trace 记录 summary 覆盖范围，避免同一消息既被摘要又被原文重复注入太多轮。

#### P2-9 CLI / Web 上下文入口一致性

CLI 支持 TaskMemory 注入；Web 示例目前主要依赖 Agno session history 与工具调用。

建议：

- ContextBuilder 应成为 CLI 与 Web 的共同入口。
- Web 是否启用 TaskMemory 可后置，但上下文层次应一致。
- 先统一上下文构建接口，再决定各入口启用哪些层。

#### P2-10 统一 token / char 预算器

Mem0、Hindsight、Asset 有局部 limit；Graphiti 有 max results；但 ContextBuilder 没有统一预算。

自用阶段短期不一定撑爆窗口，但预算器是 ContextBuilder 成熟度的关键能力。

建议：

- 为四层分配预算，例如 system 20%、retrieval 35%、working memory 25%、current anchor 20%。
- 先做字符级预算即可，后续再接 tokenizer。
- 超预算时优先压缩 Asset、Graphiti、旧 history，保留 current anchor。

#### P2-11 Query Planning

当前 `retrieve_ordered_context(query)` 对 Mem0、Hindsight、Graphiti、Asset 使用同一个 query。Query Planning 属于 1/2/4 层的 SOTA 增强，研发难度高于格式与基础预算。

风险：

- 风格类需求、事实类需求、历史教训需求混在一起。
- Hindsight 容易召回泛化经验，Asset 可能召回弱相关案例。
- 召回层无法针对不同记忆类型优化 query。

建议：

- 加入轻量 Query Planning：从当前输入中生成 `profile_query`、`lesson_query`、`knowledge_query`、`style_query`、`material_query`。
- 第一阶段可以用确定性规则或小模型；自用阶段可先做简单模板。
- trace 中记录原始 query 与各层派生 query，方便排查。

相关位置：

- `src/agent_os/agent/tools.py`
- `src/agent_os/memory/ordered_context.py`

#### P2-12 Contextual Squeezing

当前已有 Hindsight / Asset synthesis 开关，但默认关闭。长期建议对长召回结果做 query-relevant squeezing：只把与当前任务相关的要点进入主 prompt。

注意：

- 当前 Asset ordered context 默认 `include_raw=False`，并不会无条件塞入几千字原文。
- 但单独调用 `search_reference_cases(..., include_raw=True)` 时仍可能返回较长节选，需要预算控制。

#### P2-13 JIT 按需加载

JIT 的核心思想是：长知识不一定预先注入 prompt，可以只注入索引、摘要或工具入口，等模型推理到需要时再读取相关片段。

建议：

- 对 Asset / Graphiti 的长内容先提供短摘要和来源 ID。
- 需要展开时再通过工具读取具体内容。
- 自用阶段先做设计预留，不急于实现完整 JIT 知识系统。

### P2-H：V2.x Hardening（原始 P2 完成后的必要补强）

以下事项是本轮实现后仍值得近期做的最小补强。筛选原则是：只保留能验证当前 V2 是否稳定、且不会引入复杂智能裁切 / 大评测平台 / LLM planner 的事项。其余“可做但不急”的增强下放到 P3 / P4 条件触发清单。

状态：**P2-H1 / H2 / H3-mini / H4 / H5 / H6 / H7-mini / H8 / H9 / H10 已完成最小实现**。二次审查后已补齐 Web ContextBuilder 历史来源、非法 `mem_kind`、working memory / recent history 预算裁切、XML-like prompt 边界、动态块顺序、双 history 配置校验、Web Asset 自动预取对齐、非字符串 / 异常输入鲁棒性等测试。

#### P2-H1 真实多轮回放与 Agno message 结构回归

状态：**已完成最小回归用例**。

优先级：P2-H / 高。

原因：

- 当前 `clean_history_messages` 已能折叠 tool output，但 Agno 不同版本或不同 DB 后端返回的 message 对象字段可能不同。
- 如果真实 CLI / Web 多轮会话中字段变化，可能导致工具输出折叠失效或当前用户消息解包不完整。

方案：

- 增加 CLI / Web 级最小集成用例，模拟一次 `retrieve_ordered_context` 工具输出进入历史，再跑下一轮，断言 prompt 中只保留折叠摘要。
- 覆盖 tuple、对象属性、tool message 三类 session message 形态。
- 将失败样例沉淀到 `tests/core/test_context_builder.py` 或专门的 session replay fixture。

已落地：

- `tests/core/test_context_builder.py` 增加 tool output replay 折叠回归，验证大块 `retrieve_ordered_context` 结果不会原样回灌。
- 二次审查后，`examples/web_chat_fastapi.py` 的 Web ContextBuilder 历史来源改为优先读取 Agno 持久化 session DB，失败或未挂 DB 时才回退 `_transcripts`。
- `tests/core/test_web_admin_api.py` 覆盖 Web ContextBuilder 优先读取持久化历史与内存 fallback，避免跨进程 / 重启后历史展示与模型注入不一致。

协作备注：

- 若后续 Agno message 对象字段变化，只需要补 `clean_history_messages` 适配和回归 fixture；不要重新打开 P1-2 / P2-H1 的整体方案。

#### P2-H2 自动预取触发策略按 skill / 入口可配置

状态：**已完成最小实现**。

优先级：P2-H / 高。

原因：

- 当前 `should_auto_retrieve` 是关键词规则，适合自用阶段，但不同 skill 对召回的需求不同。
- 触发过宽会浪费预算，触发过窄会让 Memory V2 的召回不稳定。

方案：

- 将 `_AUTO_RETRIEVE_KEYWORDS` 提升为 Settings / skill manifest 可配置项。
- 增加 `auto_retrieve=off|keywords|always|manual` 策略。
- trace 中记录触发原因，例如 `auto_retrieve=keywords:方案`。

已落地：

- 新增 `resolve_auto_retrieve_decision`，支持 `keywords` / `always` / `manual` / `off`。
- `Settings` 支持 `AGENT_OS_CONTEXT_AUTO_RETRIEVE_MODE` 与 `AGENT_OS_CONTEXT_AUTO_RETRIEVE_KEYWORDS`。
- Skill manifest 支持 `auto_retrieve_mode` 与 `auto_retrieve_keywords` 覆盖。
- CLI / Web 在 ContextBuilder trace 中记录自动预取原因。
- 边界语义已固定：用户消息为空或纯空白时，即使 `mode=always` 也返回 `empty`，不触发召回。

验证：

- `tests/core/test_context_builder.py` 覆盖常规 auto retrieve 模式。
- `tests/core/test_p2_boundary_negative.py` 覆盖非法 mode、空关键词、空白用户消息、空元组回退默认关键词等负向路径。

#### P2-H3-mini Token 总量估算（只观测，不参与裁切）

状态：**已完成最小实现**。

优先级：P2-H / 中低。

原因：

- 当前 `ContextCharBudget` 是字符级预算，能控制趋势，但实际模型成本仍以 token 为准。
- 只做总量估算有助于判断字符预算是否失真，但不应该立刻引入 token 级裁切策略。

方案：

- 可选接入 `tiktoken` 估算最终 prompt 总 token 数；缺依赖时静默降级为仅字符数。
- 估算结果只进入 `AGENT_OS_CONTEXT_TRACE` / observability，例如 `estimated_tokens=...`。
- 不按 token 结果裁切 prompt，不配置复杂预算比例，不改变现有 `ContextCharBudget` 行为。

已落地：

- `ContextBuilder` 可选记录 `token_estimate` trace；缺 `tiktoken` 时记录 `estimated_tokens=unavailable`，不影响运行。
- `Settings` 支持 `AGENT_OS_CONTEXT_ESTIMATE_TOKENS`。

协作备注：

- H3-mini 只观测不裁切。不要把 token estimate 接入裁切逻辑，除非新增明确的后续计划和测试。

#### P2-H4 硬总预算裁剪策略

状态：**已完成默认关闭的保守实现**。

优先级：P2-H / 中高。

原因：

- 当前块级预算会截断 `working_memory`、`external_recall`、`recent_history`，并记录 `context_budget` 是否超总预算。
- 但 wrapper 标签、runtime context、attention anchor、current user message 仍可能让最终消息超过 `AGENT_OS_CONTEXT_MAX_CHARS`。

方案：

- 默认关闭，仅在 `AGENT_OS_CONTEXT_HARD_BUDGET=1` 时启用。
- 只做结构化裁切，不从 XML tag 中间硬截断：history 按旧消息删除，asset / hindsight 按低排名 item 删除，Graphiti 保留 metadata 与 omitted 标记。
- 裁切顺序：`recent_history` -> `external_recall` -> `working_memory.task_index` -> `working_memory.task_summary`。
- 永不裁切 static/system/developer 层、`attention_anchor`、最终 `current_user_message`。
- prompt 与 trace 都必须留痕，例如 `<budget_omitted block="recent_history" omitted_items="6" reason="context_budget" />`。

已落地：

- `ContextBuilder(hard_total_budget=True)` 启用结构化整块省略；默认关闭。
- 裁切只替换低优先级整块为 `<budget_omitted ... />`，不裁当前用户请求。
- `Settings` 支持 `AGENT_OS_CONTEXT_HARD_BUDGET`。

验证：

- `tests/core/test_context_builder.py` 覆盖硬预算省略低优先级块。
- `tests/core/test_p2_boundary_negative.py` 覆盖极紧硬预算下仍完整保留超长当前用户消息。

协作备注：

- 当前实现按整块省略 `recent_history` -> `external_recall` -> `working_memory`，不是 item 级智能裁切。
- `current_user_message` 不裁剪是刻意设计，用来保证当前用户请求不被历史或召回挤掉；若要限制 HTTP body，应在入口层另设 max length，而不是修改硬预算语义。

#### P2-H5 ContextBuilder 与 Agno history 配置互斥校验

状态：**已完成**。

优先级：P2-H / 中。

原因：

- 默认配置下，`enable_context_builder=True` 且 `context_self_managed_history=True`，`get_agent` 会关闭 Agno 原始 `add_history_to_context`，改由 ContextBuilder 注入清洗后的 `recent_history`。
- 但如果用户显式设置 `AGENT_OS_CONTEXT_SELF_MANAGED_HISTORY=0`，同时保持 `AGENT_OS_ENABLE_CONTEXT_BUILDER=1`，Agno 原生 history 与 ContextBuilder 动态上下文可能并存。
- 该组合不会立刻导致不可用，但可能造成历史重复、顺序不清、旧工具输出或旧目标重新获得过高注意力。

方案：

- 启动 / 构建 Agent 时检测该组合，至少输出明确 warning。
- 更保守做法：当 `enable_context_builder=True` 时，除非设置显式 override，否则强制 `context_self_managed_history=True`。
- 增加配置回归测试：默认配置不启用 Agno history；风险配置会 warning、被拒绝或需要显式 override。

已落地：

- 新增 `AGENT_OS_CONTEXT_ALLOW_AGNO_HISTORY_WITH_BUILDER` 逃生开关，默认关闭。
- 当 `enable_context_builder=True` 且 `context_self_managed_history=False` 时，默认 suppress Agno `add_history_to_context`，避免双 history；只有显式开启 override 才允许混用。
- 风险配置会写 warning，便于排查非默认配置导致的上下文异常。

验证：

- `tests/core/test_session_persistence.py` 覆盖默认抑制双 history 风险，以及显式 override 时允许 Agno history。

协作备注：

- 这是非默认配置陷阱，因此不回升到 P1；但它直接影响第 3 层 recent history 的可信边界，适合放入 V2.x hardening。

#### P2-H6 Web / CLI external recall 能力对齐

状态：**已完成**。

优先级：P2-H / 中。

原因：

- CLI 自动预取路径会按 `Settings.enable_asset_store` 和 `asset_store_from_settings` 传入 Asset Store。
- Web `/chat` 当前自动预取中仍写死 `enable_asset_store=False`、`asset_store=None`，即使全局开启 Asset Store，Web 的 `external_recall` 也不会包含 Asset 层。
- Web 与 CLI 已共享 ContextBuilder，且 Web history 来源已改为优先读取 Agno session DB；剩余差异主要是 external recall 能力开关和后续 TaskMemory 对齐。

方案：

- Web `_build_stack` 或 `/chat` 路径复用 CLI 的 `asset_store_from_settings`。
- Web 自动预取与 `get_agent` 使用同一套 Asset Store 开关和实例。
- 增加 Web 定向测试：开启 `enable_asset_store=True` 时，自动 external recall 会调用或包含 Asset Store 结果；关闭时保持当前行为。

已落地：

- Web `_build_stack` 会按 settings 构造 Asset Store，并传给 `get_agent`。
- Web `/chat` 自动预取路径复用 `asset_store_from_settings`，并按 `Settings.enable_asset_store` 传入 `build_auto_retrieval_context`。
- CLI / Web 在第 2 层 external recall 上共享同一套 Asset Store 开关语义；Web TaskMemory 仍按 V3 处理。

验证：

- `tests/core/test_web_admin_api.py` 覆盖 Web 自动预取在 `enable_asset_store=True` 时传入 Asset Store settings 与实例。

协作备注：

- Web TaskMemory 对齐仍属于 V3 复杂工作记忆线；本项只处理第 2 层 external recall 的入口一致性。

#### P2-H7-mini 边界回归评测集

状态：**已完成 mini 版**。

优先级：P2-H / 中。

原因：

- 当前 Query Planning 与 abstain 阈值有单测覆盖，但还缺少小型任务级 fixture 验证关键边界。
- 评测集如果做得过大或过主观，反而会把系统往错误方向优化；因此只做 mini 边界回归，不做质量 benchmark。

方案：

- 总量不超过 20 条 fixture，只测确定性边界，不评最终回答质量。
- 覆盖 Mem0 正/负例、Hindsight superseded、Graphiti injection 边界、Asset style/source 分离、预算保护 current user message。
- 断言只包含结构与边界：相关 item 是否进入、弱相关是否 abstain、usage_rule 是否存在、当前用户请求是否完整保留。
- 不使用 LLM-as-judge，不自动调参，不用该评测集优化文风。

已落地：

- 在 `tests/core/test_context_builder.py` 与 `tests/core/test_ordered_context.py` 中补充 deterministic 边界断言：工具输出折叠、当前请求保留、Graphiti injection 仅作为 background evidence、低相关 Graphiti abstain、Asset style/source 分离等。
- 二次审查后新增 / 扩展：
  - `tests/core/test_p2_boundary_negative.py`：空值、纯空白、`None`、超长 query、控制字符、异常 asset score、非法 auto retrieve mode 等。
  - `tests/core/test_web_admin_api.py`：`/chat` 空白 / 超长 / 畸形 JSON / 异常字符、`/ingest` 非法 target / 空白 text / 超长 text / 非法 `mem_kind`、旧版 `/api/memory/ingest` 非法 kind / 空白 text。
  - `tests/core/test_cli.py`：交互式 CLI 跳过空白输入，并保留零宽、控制字符和 XML-like 字符输入。
  - `tests/core/test_ingest_gateway.py`：`run_ingest_v1` 非法 `mem_kind`。
  - `tests/core/test_context_builder.py`：`working_memory`、`external_recall`、`recent_history` 三类块的字符预算裁切均有断言。

协作备注：

- H7-mini 是 deterministic 边界回归，不是 LLM 质量评测。不要用它优化文风或主观回答质量。

#### P2-H8 XML-like prompt 边界最小安全增强

状态：**已完成最小实现**。

优先级：P2-H / 中低。

原因：

- 当前 XML-like 结构主要服务模型理解，不是严格 XML parser 隔离。
- `attention_anchor` 与最终 `current_user_message` 会直接包含用户输入；如果用户输入伪造闭合标签、伪造高优先级块或包含 XML-like 指令，可能削弱结构边界。
- 外部召回层已有 `usage_rule` 与宪法约束，但输入 / 召回文本的结构边界仍依赖模型遵循约定。

方案：

- 对用户输入和外部召回文本采用最小可读的 literal 包裹或转义策略，避免内容伪造 ContextBuilder 标签。
- 保持对模型可读，不引入完整 XML parser、schema validation 或复杂 sanitizer 平台。
- 增加 deterministic 测试：用户输入包含 `</attention_anchor>`、`<system>`、`</current_user_message>` 等片段时，最终 prompt 的结构边界仍可解释且当前用户请求完整保留。

已落地：

- `ContextBuilder` 对当前用户输入、recent history、working memory 等 literal 内容做 XML 实体转义，避免用户文本伪造外层 prompt 标签。
- `external_recall` 保留 `ordered_context` 内部 evidence 结构，但会中和 `context_management_v2`、`external_recall`、`attention_anchor`、`current_user_message` 等 ContextBuilder 外层边界标签。
- `clean_history_messages` 只在确认上一轮内容是 ContextBuilder 包装消息时才解包 `<current_user_message>`，避免普通用户文本伪造该标签后被错误抽取。

验证：

- `tests/core/test_context_builder.py` 覆盖当前用户输入、recent history 与 external recall 中的 XML-like 边界注入。
- 定向 `python -m pytest tests/core/test_context_builder.py -q` 已通过。

协作备注：

- 本项只处理当前 V2 prompt 的必要边界安全；严格 XML schema、机器解析、统一 sanitizer 与不可信外部素材平台化治理下放 P4+。

#### P2-H9 ContextBuilder 动态块顺序对齐

状态：**已完成**。

优先级：P2-H / 中。

原因：

- 目标四层架构中，外部召回层应先于动态工作记忆层进入每轮 prompt。
- 旧实现顺序为 `runtime_context -> working_memory -> external_recall -> recent_history -> attention_anchor -> current_user_message`，虽然可运行，但与目标架构的层次表达不完全一致。
- 将 `external_recall` 前移后，模型先看到长期事实、历史教训、领域知识与素材证据，再读取当前 session/task 的工作记忆，有利于区分外部证据与会话内状态。

方案：

- `ContextBuilder.build_turn_message` 的动态块顺序调整为：
  `runtime_context -> external_recall -> working_memory -> recent_history -> attention_anchor -> current_user_message`。
- 不改变各块内容、预算策略、P2-H8 转义策略和 hard budget 裁切策略。
- 增加顺序回归测试，避免后续改动再次打乱目标层次。

已落地：

- `src/agent_os/context_builder.py` 已将 `external_recall` 拼接移动到 `working_memory` 之前。
- `tests/core/test_context_builder.py` 新增顺序断言，覆盖 runtime、external recall、working memory、recent history、attention anchor、current user message 的相对位置。

协作备注：

- 这次只调整每轮 prompt 的块顺序，不改变四层内部职责；`recent_history` 仍放在 working memory 后、attention anchor 前。

#### P2-H10 异常输入鲁棒性二轮收敛

状态：**已完成**。

优先级：P2-H / 中。

原因：

- 第二轮压力测试发现，上下文构造主路径虽然能处理 `None` 和常规字符串，但部分函数仍隐含假设输入一定是字符串。
- 典型风险包括：`user_message`、`retrieved_context`、`auto_retrieve` 的 `mode/keywords`、`TaskSummary.summary_text`、`query_plan` 的 query、TaskMemory fallback summary 等被运行时误传为数字、布尔值、bytes、对象或 `None`。
- 这些输入不属于正常业务路径，但跨入口、跨 Agent、测试替身或第三方封装中容易出现；如果直接 `.strip()`，会导致上下文构造失败，而不是优雅降级。

方案：

- 在上下文构造相关路径中统一使用文本归一：`None -> ""`，非字符串通过 `str(...)` 转换。
- 保持已定语义：超长 `current_user_message` 不由 ContextBuilder 裁剪；入口层仍负责 HTTP / CLI body 长度限制。
- 扩展负向测试和临时压力脚本，覆盖空值、非字符串、超长文本、异常字符、伪造 XML 标签和畸形 history message。

已落地：

- `ContextBuilder` 增加文本归一辅助，覆盖 `_shorten`、literal escape、history content、`user_message`、`retrieved_context`、`resolve_auto_retrieve_decision`。
- `TaskMemory` 的 `fallback_task_title`、fallback summary、`build_task_summary_instruction` 对非字符串 summary / message 内容做归一。
- `plan_retrieval_subqueries` 支持运行时误传的非字符串 query。

验证：

- `tests/core/test_p2_boundary_negative.py` 覆盖非字符串 user message、retrieved context、TaskSummary summary、query planning、`None` history content 不泄露为 `"None"`。
- 第二轮临时压力脚本覆盖 14 类 payload：`None`、空白、数字、布尔值、12 万字符超长文本、控制字符、零宽字符、BOM、伪造 ContextBuilder 闭合标签、大写 XML-like 标签、畸形 history tuple / role / bytes content / tool 无 name。
- 第二轮逻辑不变量脚本覆盖动态块顺序、外层标签唯一性、tool output 折叠、trace 块和预算标记。

协作备注：

- 该项属于“不要再循环打开”的鲁棒性收敛项。后续若出现新的异常输入类型，应补具体 fixture，而不是重新打开 P2-H8 / P2-H9 的整体方案。
- 超长当前用户输入仍按 P2-H4 既定语义完整保留；若需要限制长度，应在 CLI / Web / API 入口层设置 body 或字段上限。

### P3/P4 条件触发清单（暂不放入近期 P2）

以下事项只有在真实使用暴露明确问题后才启动，不作为当前 P2 backlog：

- Graphiti query-relevant squeezing：当 Graphiti 正文开始频繁超预算或截断丢关键信息时再做。
- Debug-only 被拒候选 trace sink：当 abstain 阈值需要系统性调参时再做。
- XML-like evidence schema / parser 校验：当调试端、评测器或外部系统需要机器解析 evidence bundle 时再做。
- XML-like prompt 安全平台化：当接入大规模不可信素材、外部 skill 包或机器解析链路时，再做统一 sanitizer、严格 schema、标签白名单和解析失败降级。
- 高级 Query Planner / Rerank：当确定性 query planning 在真实任务中明显不足，且已有 mini 评测集保护后再实验。
- 高级 auto retrieve：LLM 分类触发、学习型召回触发、智能 planner / rerank 暂冻结到 P4+；V2.x 只保留 `keywords` / `always` / `manual` / `off` 的确定性策略与 skill / settings 配置。
- Token 级预算比例配置化：若 H3-mini 观测显示字符预算长期失真，再升级；否则不做。

协作状态：以上均不是“未完成 bug”。接手 Agent 若没有新的线上 / 本地实测证据，不应把这些条目升级为当前修复任务；最多补充观察记录或新增明确的触发条件。

### Context V2.4：近期收敛线

以下事项来自本轮独立审查后确认的 P2 级以上剩余问题。它们不重新打开已完成的 V2.0-V2.3 主线，也不引入复杂 SubAgent / Rewind / LLM planner。

当前解决情况总览：

| 编号 | 问题 | 解决情况 | 回归覆盖 |
| --- | --- | --- | --- |
| P2-H11 | `attention_anchor` 与最终 `current_user_message` 长输入重复 | 已完成。anchor 只保留短锚定文本和长度标记，最终用户原文仍完整保留 | `tests/core/test_context_builder.py` |
| P2-H12 | 静态前缀仍受 handoff 生成时间影响，降低 prompt / KV cache 稳定性 | 已完成。handoff 静态摘要不再注入 `created_utc`，重复构建 agent instructions 保持稳定 | `tests/core/test_handoff.py`、`tests/core/test_p1_constitutional_output.py` |
| V2.4-3 | Web `/chat` 未接入 TaskMemory，弱于 CLI 工作记忆路径 | 已完成。Web 启用 TaskMemory 时写入 user / assistant message、注入 TaskSummary / TaskIndex，并按 summary 收紧 history cap | `tests/core/test_web_admin_api.py` |
| P2-H13 | 本轮剩余 P2 级边界 bug：极小预算、runtime 标识符注入、CLI history cap | 已完成。`_shorten` 不再超预算，runtime 标识符转义，CLI 拉取 history 使用有效 `hist_cap` | `tests/core/test_p2_boundary_negative.py`、`tests/core/test_cli.py` |

协作备注：V2.4 不是开放式 backlog。除非出现新的 P2 级以上回归，否则本节视为已收口；P3/V3 类复杂工作记忆只记录，不在本轮继续研发。

#### P2-H11 Attention Anchor Squeezing

状态：**已完成**。

优先级：P2-H / 高。

原因：

- 当前 `attention_anchor` 与最终 `current_user_message` 都包含用户原文；短请求问题不大，但长输入会造成 token 重复和注意力噪声。
- `current_user_message` 应完整保留以确保当轮用户原文不被历史或召回挤掉；重复治理应发生在 `attention_anchor`，而不是裁剪最终用户消息。
- 注意力锚定层应表达“本轮目标 / 输出要求 / 成功标准”，不应承担完整原文承载职责。

方案：

- `attention_anchor` 中的当前请求改为摘要化 / 截断后的目标锚，不再全文复制长用户输入。
- 最终 `<current_user_message>` 继续保留完整、转义后的用户原文。
- trace 中记录 anchor 是否发生 squeezing，便于观察节省效果。

验收：

- 超长用户输入在最终 `<current_user_message>` 中完整保留。
- `attention_anchor` 不再包含超长用户输入全文，而是包含短锚定文本与原始长度 / 保留长度标记。
- 现有 XML-like 转义、防标签伪造与 hard budget 语义不变。

已落地：

- `ContextBuilder` 新增 attention anchor squeezing：`<current_user_request>` 带 `mode`、`original_chars`、`kept_chars`，长输入只保留短锚定文本。
- 最终 `<current_user_message>` 仍保留完整转义后的用户原文。
- `ContextTrace` 的 `attention_anchor` block 记录 `mode=squeezed|literal` 与字符数。

验证：

- `tests/core/test_context_builder.py` 覆盖超长当前请求在 anchor 中被 squeezed、但最终 current message 保留完整正文。

#### P2-H12 Static Prefix / KV Cache Hygiene

状态：**已完成**。

优先级：P2-H / 中高。

原因：

- P1-1 已解决动态上下文混入静态 instructions 的主问题，但静态前缀仍可能因 handoff 生成时间、工具 schema 开关、每轮重建 Agent 或非必要动态 hint 变化而降低 prompt / KV cache 稳定性。
- “顶部绝对静止层”在工程上应尽量接近 run-level stable prefix：同一 skill / tool mask / settings 下，instructions 与 tools 应稳定。

方案：

- 移除或降级静态 instructions 中非必要动态字段，例如 handoff 清单生成时间。
- 增加静态前缀稳定性回归测试，验证相同 settings / skill 下重复 `get_agent` 的 instructions 稳定。
- 不把 runtime time、task summary、recent history、retrieved context 重新放回 instructions。

验收：

- 相同 manifest / handoff / settings 下，重复构建 Agent 的 `instructions` 文本稳定。
- handoff 摘要不再注入会随构建或生成时间频繁变化的字段。
- ContextBuilder 动态层语义不变。

已落地：

- `load_handoff_instruction_lines` 不再把 `created_utc` 注入静态 instructions，仅保留版本、条目数、校验统计与 schema 引用等稳定摘要。
- 增加 `get_agent` 静态前缀回归测试，验证相同 settings / handoff 下重复构建的 instructions 稳定，且不含 handoff 生成时间。

验证：

- `tests/core/test_handoff.py` 覆盖 handoff 摘要不输出 `created_utc`。
- `tests/core/test_p1_constitutional_output.py` 覆盖静态前缀稳定性与 handoff 时间字段剔除。

#### V2.4-3 Web TaskMemory Parity

状态：**已完成**。

优先级：P2-H / 中高。

原因：

- CLI 已能在启用 TaskMemory 时把 `TaskSummary`、`TaskIndex` 和 history cap 传给 ContextBuilder；Web `/chat` 目前主要依赖 persisted recent history，工作记忆能力弱于 CLI。
- 该项是入口一致性和第 3 层简单对齐，不包含复杂 task boundary、SubAgent、Rewind 或多任务回溯治理。

方案：

- Web `/chat` 在 `AGENT_OS_ENABLE_TASK_MEMORY=1` 时使用现有 `TaskMemoryStore` / `TaskSummaryService`。
- 在构造 ContextBuilder 消息前写入当前 user message，读取当前 task summary / task index，并应用 `effective_session_history_max_messages`。
- 回复后写入 assistant message 并滚动更新 summary。

验收：

- Web 路径启用 TaskMemory 后，`working_memory` 可注入当前 task summary / task index。
- 存在 TaskSummary 时，Web recent history cap 与 CLI 一致收紧。
- 不改变 TaskMemory 默认关闭语义，不引入自动 task boundary。

已落地：

- Web `/chat` 在 `Settings.enable_task_memory=True` 时创建现有 `TaskMemoryStore` / `TaskSummaryService`。
- Web 在构造 ContextBuilder prompt 前写入当前 user message，读取当前 task summary / task index，并用 `effective_session_history_max_messages` 收紧 recent history。
- Web 在模型回复后写入 assistant message，并按现有 TaskSummaryService 滚动更新 summary。

验证：

- `tests/core/test_web_admin_api.py` 覆盖 Web ContextBuilder 注入 TaskSummary / TaskIndex，并在 summary 存在时把 persisted history limit 收紧到 `AGENT_OS_SESSION_HISTORY_CAP_WHEN_TASK_SUMMARY`。

#### P2-H13 边界补丁收敛

状态：**已完成**。

优先级：P2-H / 中。

原因：

- 二次代码检查发现少量不需要进入 P3 复杂设计的可修边界问题：`_shorten` 在极小 `max_chars` 下可能返回超过预算的字符串；`runtime_context` 中 client / user / skill 等标识符未转义时可能伪造 XML-like 边界；CLI 在已有 TaskSummary 时已计算 `hist_cap`，但拉取 Agno session history 仍使用原始 `session_history_max_messages`。
- 这些问题属于当前 V2 上下文工程的边界稳定性与入口一致性，不涉及 SubAgent、Rewind、自动 task boundary 或平台化治理，因此按 P2-H 直接修复。

已落地：

- `ContextBuilder._shorten` 与 TaskMemory fallback `_shorten` 保证返回长度不超过 `max_chars`；`max_chars<=0` 返回空串，`1..3` 使用硬截断，不追加超预算省略号。
- `build_ephemeral_instruction` 对 `timezone_name`、`entrypoint`、`skill_id`、`client_id`、`user_id` 做 XML 实体转义，避免 runtime context 内部字段破坏外层 prompt 结构。
- CLI `_session_messages_for_context` 改为接收并使用有效 `hist_cap`；TaskSummary 存在时，DB 拉取 limit 与 ContextBuilder 的 `history_max_messages_override` 一致。

验证：

- `tests/core/test_p2_boundary_negative.py` 覆盖 `_shorten` 极小预算不超长，以及 runtime context 标识符伪造闭合标签时外层标签仍唯一。
- `tests/core/test_cli.py` 覆盖 CLI 在 TaskSummary 存在时按 `AGENT_OS_SESSION_HISTORY_CAP_WHEN_TASK_SUMMARY` 拉取历史。
- 定向验证已通过：`pytest tests/core/test_p2_boundary_negative.py tests/core/test_cli.py tests/core/test_context_builder.py -q`、`pytest tests/core/test_web_admin_api.py tests/core/test_handoff.py tests/core/test_p1_constitutional_output.py -q`、相关文件 `ruff check` / `ruff format --check` 与 `ReadLints`。

### Context V2.5：独立评审新增项

以下事项来自本轮对代码状态的独立复核。它们不否定 V2.0-V2.4 的验收结论，也不重开原始 P1 / P2 主线；处理策略是：能直接改善四层上下文强架构的，进入 P2-H 优先执行；涉及复杂工作记忆、生产并发或平台鲁棒性的，先记录到 P3/P4；低风险小项可在改相关文件时顺手补。

当前分类总览：

| 编号 | 问题 | 优先级 | 当前处理 |
| --- | --- | --- | --- |
| P2-H14 | 顶部静止层仍混入非必要动态摘要，且 handoff 摘要位于 system instructions，和其实际领域 SOP 权威层级不完全匹配 | P2-H / 高 | 已完成 |
| P2-H15 | `attention_anchor` 已避免长输入重复，但目标 / 约束 / 输出格式仍主要是固定模板，缺少轻量语义抽取 | P2-H / 高 | 已完成 |
| P2-H16 | recent history 对 assistant 长交付物默认 800 字硬截断，影响下一轮基于上一版内容续写 / 修改 | P2-H / 中高 | 已完成 |
| P2-H17 | 自动 external recall 与手动 `retrieve_ordered_context` 工具双轨并存，缺少“本轮已预取，不必重复召回”的提示；同时缺少 record_* 工具边界锚定 | P2-H / 中 | 已完成 |
| P2-H18 | Web `/chat` 自动召回路径每轮重建 Graphiti / Asset Store 客户端，和 `_build_stack` 缓存不一致 | P2-H / 中 | 已完成 |
| V2.5-small | query plan 与实际 Hindsight query 标注不一致、英文 auto retrieve 触发词不足、超长 current message 缺观测提示、`get_agent(entrypoint=...)` 在 ContextBuilder 默认开启时近似死参；审查反馈指出的英文子串误匹配、零宽关键词和 auto retrieve 关闭态 trace 语义 | 小项 / 低 | 已完成 |
| V2.5-small-followup | handbook version 是否仍应进入静态 instructions、全 abstain 时 external recall 空壳提示、attention anchor 固定模板压缩、跨 entrypoint 工具 mask 稳定性说明 | 小项 / 低 | 已完成 |
| C-test-hardening | 模型遵循度 / prompt 边界的 deterministic fixture、外部依赖异常软降级、空值 / 超长文本 / 异常字符压力输入、上下文管理矩阵回归 | 测试 / 低 | 已完成本轮基础覆盖 |
| V3-record | Web 多 worker + SQLite session DB 可能回退为内存 history；自动召回缺 timeout / 分层软降级；SubAgent 结果 / Todo / 中间产物尚无独立工作记忆通道；tool protocol replay 与 history bloat 的取舍需要 V3 统一治理 | P3/P4 记录 | 先不做 |

#### P2-H14 Static Prefix Residual Dynamics / Handoff Layering

状态：**已完成**。

优先级：P2-H / 高。

原因：

- P2-H12 已移除 handoff `created_utc`，但静态 instructions 里仍包含 handoff 条目数 / 校验统计、`golden_rules` 条数、Graphiti 是否挂载等状态摘要。
- 这些信息不是系统规则本身，随文件内容、运行环境或外部服务挂载状态变化，会降低“顶部绝对静止层”的 cache 稳定性。
- handoff 在系统宪法中属于领域 SOP / 交付资料层，但当前由 `get_agent` 拼入 system instructions，权威位置高于其语义层级。

方案：

- 将 handoff 统计、`golden_rules` 数量、Graphiti 未挂载提示等非规则状态从静态 instructions 移出，优先放入 runtime context、external recall metadata 或 observability。
- 若 handoff 内容未来承载 SOP / 资料，应以 `usage_rule=background_only` 或 `authority=domain_sop` 形式进入 external recall，而不是长期钉在 system 顶部。
- 保留真正稳定的 constitutional rule、skill contract、manifest system prompt 与必要工具 schema。

已落地：

- ContextBuilder 默认开启时，`get_agent` 不再把 handoff 状态摘要、`golden_rules` 条数和 Graphiti 未挂载提示注入静态 instructions。
- legacy 模式仍保留 handoff 状态摘要兼容路径。
- `get_agent` docstring 说明 `entrypoint` 在 ContextBuilder 默认开启时由动态 runtime context 使用。

验收：

- 相同 skill / tool mask / manifest 规则下，非必要运行状态变化不改变静态 instructions。
- handoff 不再因统计字段或资料型内容获得高于当前用户指令的 system 级注意力。
- 现有 V2.4 静态前缀稳定性测试扩展覆盖 residual dynamics。

#### P2-H15 Attention Anchor Semantic Extraction

状态：**已完成**。

优先级：P2-H / 高。

原因：

- P2-H11 已解决 `attention_anchor` 与最终 `current_user_message` 的长输入全文重复问题，但 anchor 中的 `<goal>`、`<must_follow_now>`、`<success_criteria>` 仍主要是固定模板。
- 注意力锚定层应表达本轮真实目标、硬约束、输出格式、禁止项与成功标准；仅提示“以当前用户为准”收益有限。
- 该项可用确定性规则提取，不需要引入 LLM planner、复杂 rerank 或 V3 工作记忆系统。

方案：

- 增加轻量规则 extractor，从当前用户输入中提取格式要求、数量限制、必须 / 不要、语气、范围、验收标准等短锚点。
- 将提取结果写入 `attention_anchor` 的 `<must_follow_now>` / `<success_criteria>`，未命中时保留当前默认模板。
- trace 记录 anchor 是否命中语义提取，以及提取到的约束数量。

已落地：

- `ContextBuilder` 增加确定性约束提取，覆盖格式、语言、数量限制、必须 / 禁止项等轻量锚点。
- `attention_anchor` 输出 `<extracted_constraints>`，trace 记录 `constraints=N`。
- 约束抽取已改为英文词边界匹配，避免 `stable` 误触发 `table`、`bulletin` 误触发 `bullet`；关键词匹配会移除零宽字符后再判断。

验收：

- 对“用中文、只列 3 点、不要表格、先给结论”等输入，anchor 能结构化体现这些约束。
- 超长用户输入仍只在 anchor 中保留短锚，不复制全文；最终 `<current_user_message>` 继续完整保留。
- 不改变现有 hard budget 与 XML-like 转义语义。

#### P2-H16 Recent Assistant Deliverable Retention

状态：**已完成**。

优先级：P2-H / 中高。

原因：

- 当前 `clean_history_messages` 对 user / assistant 历史统一按 `max_content_chars` 截断，默认 800 字。
- 如果上一轮 assistant 输出的是长方案、文档、脚本或结构化交付物，下一轮“基于上一版修改 / 继续扩写 / 改格式”时，模型只能看到前段内容。
- TaskSummary 默认要达到一定消息数才生成，无法覆盖长内容迭代的早期轮次。

方案：

- 对最近 1-2 条 assistant message 设置更高保留上限，或按 role / recency 分配不同 history cap。
- 更早的 assistant 历史仍按短摘要截断，避免历史长交付物反复回灌。
- trace 中标注 recent assistant 是否使用 extended cap。

已落地：

- `clean_history_messages` 支持 `max_recent_assistant_chars` 与 `recent_assistant_extended_count`。
- `ContextBuilder` 默认对最近 1 条 assistant message 使用更高保留上限，同时保留 tool output 折叠。

验收：

- 最近一轮长交付物在下一轮 prompt 中保留足够正文用于局部修改。
- 工具输出仍继续折叠，不因 assistant extended cap 导致 tool replay 回灌。
- 存在 TaskSummary 时仍能收紧整体 history 条数。

#### P2-H17 Auto Retrieve / Manual Tool De-dup and Tool Boundary Anchor

状态：**已完成**。

优先级：P2-H / 中。

原因：

- ContextBuilder 自动预取 external recall 后，`retrieve_ordered_context` 工具仍可能被模型再次调用，造成重复召回、重复 token 和额外延迟。
- 默认 manifest 暴露 `record_client_fact`、`record_client_preference`、`record_task_feedback`，虽然 Memory Policy 有兜底，但缺少当轮 anchor 对“何时不应主动写记忆”的低位提醒。
- 该问题位于工具定义层与注意力锚定层之间，应通过提示与 trace 降低误调用，而不是移除工具能力。

方案：

- 当本轮 auto retrieve 已注入 `external_recall` 时，在 trace 或 prompt 中明确“本轮已自动召回；仅在需要不同 query / 不同维度时再调用 `retrieve_ordered_context`”。
- 在 `attention_anchor` 中加入轻量工具边界：除非用户明确表达长期有效事实或反馈，不要主动调用 `record_*`。
- 保持工具 schema 与 manifest 白名单语义不变。

已落地：

- 自动召回已注入时，`external_recall` 内增加 `<auto_retrieve_hint>`，提示仅在不同 query / 不同维度时再调 `retrieve_ordered_context`。
- `attention_anchor` 增加 `<tool_boundary>`，提示不要在缺少明确长期事实或反馈时主动调用 `record_*`。
- CLI / Web 在 `enable_context_auto_retrieve=False` 时不再把关键词决策 reason 传入 `ContextBuilder`，避免 trace 中出现未实际预取的 `auto_retrieve` 块。

验收：

- 自动预取命中时，prompt 可见去重提示；未自动预取时不产生误导。
- `record_*` 的边界提示不影响用户明确要求写入长期记忆或任务反馈的场景。
- 新增测试覆盖 auto retrieve reason 与 anchor 工具边界。

#### P2-H18 Web Auto Recall Client Reuse

状态：**已完成**。

优先级：P2-H / 中。

原因：

- CLI 在循环外构造 `GraphitiReadService` 与 Asset Store；Web `/chat` 自动召回路径每轮重新调用 `GraphitiReadService.from_env` 与 `asset_store_from_settings`。
- 这会放大短轮次延迟，增加 Neo4j / LanceDB 初始化与连接抖动概率，也与 `_build_stack` 的 bundle 缓存语义不一致。

方案：

- Web `_build_stack` 返回或缓存 `knowledge` 与 `asset_store`，供 `/chat` 自动召回路径复用。
- 保持 settings 开关语义不变：`enable_asset_store=False` 时不构造 Asset Store；`no_knowledge=True` 时不构造 Graphiti reader。
- 增加 Web 定向测试，验证自动召回复用 stack 中的实例，而不是每轮重建。

已落地：

- Web `_build_stack` 将已构造的 `knowledge` 与 `asset_store` 附着到 cached agent。
- `/chat` 自动召回路径优先复用 cached agent 上的实例，仅在旧测试替身或未挂载时回退构造。

验收：

- `/chat` 自动召回路径与 agent 工具路径使用同一组 knowledge / asset store 实例。
- CLI / Web 在 external recall 能力上保持一致。
- 不引入新的全局单例或跨租户共享风险。

#### V2.5-small 低风险顺手项

状态：**已完成**。

- `query_plan.lesson_query` 当前只展示、不直接用于 Hindsight 检索；已在 `<query_plan>` 标注 `hindsight_used_query="raw"`，避免调参误解。
- auto retrieve 默认关键词已补英文常见词（plan / strategy / optimize / design / summarize / write / draft 等），并改为大小写不敏感、英文词边界匹配；中文 / 英文关键词中夹入零宽字符时会先归一再判断。
- 已增加负例保护：`planet` / `plant` 不再误触发 `plan`，`stable` 不再误抽取 `table` 约束，`bulletin` 不再误抽取 `bullet` 约束。
- 超长 `current_user_message` 仍按既定语义完整保留；已在 trace 中增加 `current_message_high_ratio` 观测，不改变裁切语义。
- `get_agent(entrypoint=...)` 在 ContextBuilder 默认开启时不直接影响 runtime context；已补 docstring 注释，避免误解。
- `ContextTrace.to_obs_log_line()` 已清洗换行与 `|`，保持 observability 单行稳定。
- `ordered_context` 已对外部适配器异常返回做归一：Graphiti 返回 `None`、Asset 返回 `None` / 单对象、Hindsight 返回非字符串时不崩溃，不泄漏 `"None"`。

协作备注：

- 不要重新打开英文 auto retrieve 大小写 / 子串误匹配、零宽关键词、trace 单行清洗、异常 external result 归一；这些已有 fixture。
- 若未来发现新的语言或符号边界，只补具体 keyword / extractor fixture，不要把 V2.5-small 整体重开为 planner / rerank 项。

#### V2.5-small follow-up：最新独立评审 A 类小项

状态：**已完成**。

这些事项属于四层架构已经成立后的实现毛刺或提示词经济性优化，不构成 Context V2 架构缺陷，也不重开 V2.0-V2.5 主线。本轮已按 A-1 ~ A-4 顺序完成：

- A-1：ContextBuilder 默认开启时，`get_agent` 不再把 `handbook_version` 注入静态 instructions；legacy instructions 路径保留该字段，避免破坏旧模式。
- A-2：Web 与 CLI 的工具 mask 差异被固定为 entrypoint / tool-mask 作用域差异；新增回归测试确认 instructions 本身稳定，工具定义差异由显式 `exclude_tool_names` 决定。
- A-3：`ordered_context` 顶层增加 `injected_evidence="true|false"`；ContextBuilder 遇到 `injected_evidence="false"` 时不再把 verbose empty shell 注入 `<external_recall>`，trace 记录 `no_injected_evidence`。
- A-4：压缩 `attention_anchor` 固定模板，保留本轮目标优先级、工具边界、`record_*` 写入边界和自动召回去重提示，减少每轮重复 token。

验证：

- `tests/core/test_p1_constitutional_output.py` 覆盖 ContextBuilder 默认静态 instructions 不含 handoff 状态 / handbook version、legacy 路径兼容保留、entrypoint 工具 mask 差异。
- `tests/core/test_context_builder.py` 覆盖空 ordered context shell 不注入 external recall、trace 记录 `no_injected_evidence`、anchor 语义仍保留。
- `tests/core/test_ordered_context.py` 覆盖 `injected_evidence="true|false"` 顶层标记。
- 定向通过：`python -m pytest tests/core/test_p1_constitutional_output.py tests/core/test_context_builder.py tests/core/test_ordered_context.py -q`。

#### V3/P4 记录项：暂不执行

状态：**记录，不进入当前 P2-H 执行范围**。

- Web 多 worker + SQLite session DB 在并发写入时可能触发锁等待或读失败；当前可先在 operations 文档记录“多 worker / 多实例请使用 PostgreSQL / Redis session DB”，真正并发治理归 P4+。
- 自动召回缺少 per-layer timeout 与软降级；自用阶段先观察，若 Mem0 / Graphiti / Asset 抖动开始影响常规对话，再进入 P3/P4 工程鲁棒性线。
- SubAgent 结果、Todo 状态和中间产物尚无独立工作记忆通道；这属于 V3 复杂工作记忆线，不应混入当前 V2.5 的 P2-H 修补。
- 当前 `clean_history_messages` 选择把 Agno tool output 折叠为文本摘要，牺牲 protocol-level tool replay，换取防历史回灌和预算稳定。这是合理 trade-off；若未来需要“可恢复工具链状态”，应在 V3 设计专门的 tool result digest / artifact reference 通道，而不是恢复原始大块工具输出回灌。
- XML-like evidence、`usage_rule` 与 `attention_anchor` 是当前阶段的软边界。严格 XML parser、schema validation、统一 sanitizer、标签白名单和外部素材平台化治理仍归 P4+；只有当外部系统需要机器解析 evidence 或接入大量不可信素材时再启动。

#### C-test-hardening：测试与压力复核

状态：**已完成本轮基础覆盖**。

本轮针对 C 类问题补充 deterministic / 负向 / 压力测试，不引入 LLM-as-judge，也不把模型主观输出质量纳入单测：

- `tests/core/test_context_builder.py` 新增 C 类 prompt 边界 fixture：确认 `usage_rule=evidence_only`、当前用户优先级、`tool_boundary`、自动召回去重提示、抽取约束在同一轮 prompt 中可见。
- `tests/core/test_p2_boundary_negative.py` 新增外部依赖异常软降级 fixture：Mem0 profile、Hindsight、Graphiti、Asset Store 抛异常时，`ordered_context` 不崩溃，输出 `relevance="error"` 与 error marker，且不泄漏异常详情。
- `tests/core/test_p2_boundary_negative.py` 新增混合恶意 payload 压力 fixture：覆盖 `None`、超长文本、控制字符、零宽字符、BOM、伪造 `context_management_v2` / `current_user_message` / `attention_anchor` 标签、畸形 history、tool output 与 hard budget 组合。
- 压力脚本额外覆盖 6 类 payload 循环构造：`None`、空串、纯空白、伪造 XML-like 标签、12 万字符超长文本、控制字符 / 零宽 / BOM 混合输入；验证外层标签唯一、当前用户消息边界和 budget trace 不崩。
- 后续只读复查反馈已补齐：`injected_evidence=false` 的空格 / 单引号 / 无引号 / 大小写变体均会被识别为空 ordered context，不再注入 `<external_recall>`；Graphiti 返回空白时标记为 `relevance="empty"` 而不是 `abstained`；hard budget 在当前用户消息占主导时仍可能 `over_budget` 的设计语义已用回归测试固定；显式允许 Agno history 与 ContextBuilder 混用的逃生配置会使用原始 Agno `session_history_max_messages`，该行为已有测试说明。

本轮代码复查修复：

- `runtime_context.build_ephemeral_instruction` 原先假设 `timezone_name`、`entrypoint`、`skill_id`、`client_id` 一定是字符串；压力输入发现运行时误传 `None` 会崩溃。已改为文本归一后再 XML escape，并补 `None` 标识符回归测试。
- `ordered_context` 原先没有捕获外部 adapter 抛出的异常。已增加 per-layer exception soft fallback：异常层输出 error marker，`injected_evidence="false"`，ContextBuilder 不把纯错误 / 空壳召回注入主 prompt。注意：这不是完整 timeout 治理，per-layer timeout 仍归 V3/P4。
- `_ORDERED_CONTEXT_EMPTY_RE` 原先只匹配紧凑的 `injected_evidence="false"`，已放宽支持属性空格、单引号、无引号和大小写变体。
- Graphiti 空字符串 / 纯空白返回原先会被 relevance gate 归入 `abstained`，现已改为 `<empty />` / `relevance="empty"`，避免把“无内容”误读为“低相关拒绝”。

验证：

- 定向通过：`python -m pytest tests/core/test_context_builder.py tests/core/test_p2_boundary_negative.py tests/core/test_ordered_context.py -q`。
- 上下文管理矩阵通过：`python -m pytest tests/core/test_context_builder.py tests/core/test_p2_boundary_negative.py tests/core/test_ordered_context.py tests/core/test_web_admin_api.py tests/core/test_cli.py tests/core/test_session_persistence.py tests/core/test_p1_constitutional_output.py -q`。
- 后续复查补测通过：`python -m pytest tests/core/test_context_builder.py tests/core/test_ordered_context.py tests/core/test_session_persistence.py tests/core/test_p2_boundary_negative.py -q`。
- 相关文件 `ruff check` 与 `ReadLints` 均通过。
- 全量 `python -m pytest tests/core -q` 当前被非上下文管理用例 `test_ingest_asset_store_minimal_with_allow_llm_off` 阻塞：该用例在 `AGENT_OS_INGEST_ALLOW_LLM=0` 下仍触发真实 OpenAI embedding 并因网络握手超时失败；这不是 ContextBuilder / ordered context 回归。

### Context V2.6：上下文工程独立评审第二轮新增项

本轮以"标准强四层上下文架构"为基线对当前实现做端到端独立评审，结论是 V2.0 ~ V2.5 的 1 / 2 / 4 层主线仍然成立，但在四层之间的耦合点上存在若干可定向消除的毛刺：跨 entrypoint 静态前缀漂移、自动召回与工具的真正互斥、prompt cache 抖动、注意力锚定层的目标复述缺位、recent history 一刀切折叠、第 3 层默认空白、manifest 静默暴露全工具。这些事项不重开 V2.0 ~ V2.5 主线，处理策略是：能直接改善四层强架构的进入 P2-H 优先执行；自动召回 per-layer timeout / LLM rerank / SubAgent 结果通道 / 真正的自动 task boundary 仍归 V3 / P4。

层级 SOTA 自评（评审第二轮口径）：

- 第 1 层（顶部静止）：方案接近 SOTA。剩余的两个具体毛刺是 Web `_WEB_EXTRA_INSTRUCTIONS` 把"演示提示"注入静态 instructions（跨 entrypoint 静态前缀不一致）、`runtime_context` 仍带秒级时间（破坏 prompt cache 前缀稳定性）。
- 第 2 层（外部召回）：方案 SOTA（XML-like + abstain + 来源标注 + JIT），但运行时治理仍未到位：自动召回与 `retrieve_ordered_context` 工具只靠 prompt 提示去重，没有真正屏蔽工具；per-layer timeout、LLM rerank、自适应 budget 仍归 V3 / P4。
- 第 3 层（动态工作记忆）：明确仍是半成品。`enable_task_memory` 默认 OFF，TaskSummary / TaskIndex 不出现，本层会完全为空；`clean_history_messages` 对所有 tool output 一刀切折叠；SubAgent / Todo / Plan / artifact reference 通道无（保留在 V3）。
- 第 4 层（注意力锚定）：骨架 SOTA（current_user_request / extracted_constraints / tool_boundary / success_criteria），内容仍是半成品：缺 `<restated_goal>` 目标复述；用户特定边界（如 Web 演示）应在此层而不是静态层。

当前分类总览：

| 编号 | 问题 | 优先级 | 当前处理 |
| --- | --- | --- | --- |
| P2-H19 | Web `_WEB_EXTRA_INSTRUCTIONS` 注入静态 instructions，跨 entrypoint 顶部静止层不一致；演示 / 边界提示应进 attention_anchor 而不是 system prefix | P2-H / 高 | 已完成 |
| P2-H20 | 自动召回命中后，`retrieve_ordered_context` 工具仍暴露给模型，仅靠 `<auto_retrieve_hint>` 提示去重；缺真正的工具互斥 | P2-H / 高 | 已完成 |
| P2-H21 | `runtime_context` 注入秒级时间，每轮变化进入第 1 层范围内的动态前缀，影响 prompt cache 命中与稳定性认知 | P2-H / 中高 | 已完成 |
| P2-H22 | `attention_anchor` 仍以固定模板 `<goal>` 提供模糊目标，缺少基于当轮用户输入的 `<restated_goal>` 目标复述 | P2-H / 中高 | 已完成 |
| P2-H23 | `clean_history_messages` 对所有 tool output 一刀切折叠到 240 字，短而结构化的工具结果（fixture probe / golden_rules 检查）也被压成"已折叠"，模型无法基于上一轮 tool 结果继续推理 | P2-H / 中 | 已完成 |
| P2-H24 | `enable_task_memory` 默认 OFF 时，`<working_memory>` 完全为空，第 3 层退化；缺 `<last_deliverable>` 之类的轻量降级填充 | P2-H / 中 | 已完成 |
| P2-H25 | manifest 未命中（skill 在 registry 找不到对应条目）时，`enabled_tool_name_set` 返回 `None` 静默暴露全部工具，没有 warn / 观测信号 | P2-H / 低 | 已完成 |
| V3-record-2 | 自动召回缺 per-layer timeout / 软降级；自用阶段先观察 | V3 / P4 记录 | 不在本轮做 |
| V3-record-3 | 真正的 SubAgent / Todo / Plan / artifact reference 通道；`<last_deliverable>` 仅是降级填充，非完整 V3 工作记忆 | V3 记录 | 不在本轮做 |
| V3-record-4 | LLM rerank、学习型 auto retrieve、严格 XML schema / parser、统一 sanitizer | V3 / P4 记录 | 不在本轮做 |

#### P2-H19 Static Prefix Cross-Entrypoint Hygiene

状态：**已完成**。

优先级：P2-H / 高。

原因：

- `examples/web_chat_fastapi.py` 通过 `extra_instructions=list(_WEB_EXTRA_INSTRUCTIONS)` 把"Web 演示已关闭三个 record_* 工具，请勿假装已写记忆"提示放进 Agno 静态 `instructions`。
- 这意味着 Web、CLI、API 三个 entrypoint 的顶部静止层不一致，静态 prefix 跨入口漂移；同时这条提示语义上属于"当轮工具边界"，归注意力锚定层而非系统宪法层。
- 顶部静止层混入演示场景特定提示，会让 prompt cache 在跨 entrypoint 复用时失效，且模型把演示提示与系统宪法看作同等权威。

方案：

- `ContextBuilder.build_turn_message` 增加 `entrypoint_extra_lines` 参数，在 `<attention_anchor>` 末尾以 `<entrypoint_notice>` 块注入入口特定的演示 / 边界提示。
- Web `_build_stack` 不再把 `_WEB_EXTRA_INSTRUCTIONS` 通过 `extra_instructions` 注入静态层；改在 `/chat` 调用 `build_turn_message` 时传入 `entrypoint_extra_lines=_WEB_EXTRA_INSTRUCTIONS`（若未来新增其他 Web 入口也走 ContextBuilder，应按同样方式传入，而不是回到静态 instructions）。
- 保持 manifest `system_prompt`、constitutional blocks、skill contract 这些真正的静态规则不变。

已落地：

- `ContextBuilder.build_turn_message` 接受 `entrypoint_extra_lines: Sequence[str] | None`，在 `<attention_anchor>` 内输出 `<entrypoint_notice>`；trace 记录 `entrypoint_notice=N`。
- Web `_build_stack` 在 `enable_context_builder=True` 时不再注入 `extra_instructions=list(_WEB_EXTRA_INSTRUCTIONS)`；仅 legacy（ContextBuilder 关闭）兼容路径保留该静态提示。
- Web `/chat` 调用 `build_turn_message` 时传入 `entrypoint_extra_lines=_WEB_EXTRA_INSTRUCTIONS`，使提示进入 `<attention_anchor>` 的 `<entrypoint_notice>`。
- 顶部静态 instructions 在 Web / CLI / API 三个入口下相同（前提：相同 skill / manifest / golden rules）。

验收：

- 同一 skill / manifest 下，Web 与 CLI agent 的 `instructions` 列表（剔除入口无关项）等同。
- Web `/chat` 的最终 prompt 中，`_WEB_EXTRA_INSTRUCTIONS` 内容出现在 `<attention_anchor>` 内 `<entrypoint_notice>`，而不在 `<context_management_v2>` 之外的 system 段。
- 测试覆盖：CLI / Web 静态 instructions 等同；`<entrypoint_notice>` 内容、转义、空白行处理。

#### P2-H20 Auto Retrieve / Tool Mutual Exclusion

状态：**已完成**。

优先级：P2-H / 高。

原因：

- 自动召回命中后，`<external_recall>` 块已通过 `<auto_retrieve_hint>` 提示模型不要重复调用 `retrieve_ordered_context`，但工具依然暴露在 schema 中，模型仍可能在同一轮再次调用，造成重复 token、重复延迟。
- 双轨并存"提示去重"是软约束，缺乏真正的工具互斥机制；理想做法是命中自动召回的轮次直接屏蔽 `retrieve_ordered_context` 工具，让模型物理上无法重复调用。

方案：

- `build_turn_message` 在进入时先清零本轮的 contextvar flag；在自动召回成功注入 `<external_recall>` 时写入 contextvar（reason 字符串）。`retrieve_ordered_context` 工具进入时若读到该 flag，直接返回固定 stub（`auto_retrieved_already_injected: ...`），并不真正访问 Mem0 / Hindsight / Graphiti / Asset。
- 因为每轮 `build_turn_message` 起步都会清零 flag，所以不依赖调用方显式 reset，避免跨轮泄漏与协作误用。
- 工具的 schema 仍暴露，保持向后兼容；模型仍可在自动召回未命中或"明确换 query / 维度"时调用。

已落地：

- 新增 `agent_os.context_builder._AUTO_RETRIEVE_ACTIVE` contextvar 与 `set_auto_retrieve_active(reason)` / `auto_retrieve_active_reason()` / 每轮起步清零帮助函数。
- `ContextBuilder.build_turn_message` 在每轮开始清零 flag；在 `retrieval_has_evidence and auto_retrieve_reason` 时写入 flag。
- `agent_os.agent.tools.retrieve_ordered_context` 在 flag 命中时直接返回 stub，避免重复召回。
- 测试覆盖：自动召回命中时工具调用得到 stub；未命中时工具仍正常返回；无需调用方 reset 也不会跨轮污染。

验收：

- 自动召回命中的轮次，`retrieve_ordered_context` 工具不会重新跑召回；trace 记录 stub 命中。
- 自动召回未命中或显式 `auto_retrieve=off` 时，工具行为与之前一致。
- 不破坏现有 `<auto_retrieve_hint>` 文本提示与 `usage_rule=evidence_only` 行为。

#### P2-H21 Runtime Context KV Cache Hygiene

状态：**已完成**。

优先级：P2-H / 中高。

原因：

- `runtime_context.build_ephemeral_context` 当前用 `local.strftime("%Y-%m-%d %H:%M:%S %Z")` 生成秒级时间。
- 该字段进入每轮 `<runtime_context>` 块（位于第 1 层范围内的高位动态前缀），秒级粒度意味着两次完全相同的对话内容也会因时间不同导致 prompt 字面量不同，长期摧毁 KV cache 命中。
- 自用阶段对"是否精确到秒"没有需求；模型只需要"日期 + 小时 + 分钟"作为排期 / 时效推理依据。

方案：

- 把秒级时间格式改为分钟级 `"%Y-%m-%d %H:%M %Z"`，保留时区与本地周次。
- 测试同步更新，避免误以为是回归。

已落地：

- `runtime_context.build_ephemeral_context` 改为分钟级；`<runtime_context>` 内时间字段不再每秒变。
- trace 中的 ephemeral chars 因长度变化做了同步更新。
- 测试覆盖：相同 client / user / skill / 同一分钟内多轮调用 `build_turn_message`，runtime context 字符相同。

验收：

- prompt cache 前缀稳定性显著提升：常规对话节奏（< 1 分钟一轮）下 runtime context 字段保持不变。
- 仍可基于本地分钟级时间做排期推理；不影响"以上信息只用于本轮推理与排期判断"语义。
- 不影响其他第 1 层稳定项（constitutional blocks / skill contract / manifest system prompt）。

#### P2-H22 Attention Anchor Restated Goal

状态：**已完成**。

优先级：P2-H / 中高。

原因：

- `<attention_anchor>` 当前提供 `<current_user_request>`（用户原文压缩 / 完整短文）+ `<extracted_constraints>` + 固定模板 `<goal>` / `<must_follow_now>` / `<success_criteria>`。
- 固定模板 `<goal>优先完成本轮请求；与历史、召回冲突时，以本轮明确指令为准。</goal>` 是元约束，不是当轮目标本身。
- 真正影响模型注意力收敛的是"用一句简洁的话复述本轮目标"，避免长输入下模型被结构 / 装饰 / 礼貌语稀释主目标。

方案：

- 增加确定性 `_extract_restated_goal(user_message)`：取去 stopword 后第一句 / 第一行 / 不超过 120 字符的核心动作短语；空输入或极短输入直接返回空。
- 在 `<attention_anchor>` 中输出 `<restated_goal>`（仅当抽取到非空内容时），位于 `<current_user_request>` 之后、`<extracted_constraints>` 之前。
- trace 记录 `restated_goal_chars=N`，便于观察是否抽到内容。

已落地：

- `_extract_restated_goal` 实现：去除装饰副词、礼貌语、问候，取首句或首行 ≤ 120 字符；保留 XML escape。
- `<attention_anchor>` 输出 `<restated_goal>`；空字符不输出该块。
- trace `attention_anchor` note 增加 `restated_goal=<chars>`。

验收：

- 长输入（> 480 字符）下，`<restated_goal>` 给出 ≤ 120 字符的目标句，与 `<current_user_request>` 分工明确。
- 空 / 纯空白输入不产生空 `<restated_goal>`。
- 不破坏现有 `<extracted_constraints>` / `<tool_boundary>` / `<success_criteria>` 顺序。

#### P2-H23 Granular Tool Output Folding

状态：**已完成**。

优先级：P2-H / 中。

原因：

- `clean_history_messages` 当前对所有 `role == "tool"` 的消息一律折叠为 240 字符 `[工具结果已折叠，仅保留摘要]`。
- 部分工具结果是结构化、短文本（如 `check_delivery_text`、`check_skill_compliance_text`、`fixture_probe`），全文常常 < 240 字，强行折叠让 prompt 看起来像被截断了，模型也无法基于"工具刚才命中了哪些规则"继续推理。
- 折叠的真正目的是防止"大块召回 / Asset 检索结果"反复回灌；对于结构化短输出该机制是过度防御。

方案：

- 在 `clean_history_messages` 增加白名单：当 tool output 长度 ≤ `max_tool_output_chars` 时，直接保留原文（仍做 XML escape）；只有超过门槛才折叠并标 `[工具结果已折叠]`。
- 引入 `tool_output_keep_full_below_chars` 参数（默认沿用 `max_tool_output_chars`），便于后续按 entrypoint 调参。
- 折叠提示文案保持 `[工具结果已折叠，仅保留摘要]`，提示模型该轮工具输出可能不完整。

已落地：

- `clean_history_messages` 在短 tool output 路径输出 `- tool:<name>: <escaped content>`，无折叠提示。
- 长 tool output 仍保留原折叠语义；trace `recent_history` note 增加 `tool_fold=<chars>` 沿用，无新增字段。
- 测试覆盖：短 tool output 不再带 "[工具结果已折叠" 提示；长 tool output 仍折叠。

验收：

- 100 字、含命中规则列表的 tool output 在 prompt 中以原文出现，模型可基于该结果做下一步推理。
- 1 万字 Asset / Graphiti / Mem0 检索类 tool output 仍折叠为短摘要。
- 防历史回灌仍然成立：长 tool output 不会反复进入下一轮。

#### P2-H24 Working Memory Default Fallback

状态：**已完成**。

优先级：P2-H / 中。

原因：

- `enable_task_memory=False` 默认下，`<working_memory>` 块不会注入；如果 history 也为空（首轮），第 3 层在结构上完全不存在。
- 多轮自用对话的真正"上一版交付物"信息，常常是上一轮 assistant 的输出本身；当 TaskSummary 没有触发时，模型无法快速感知到"上一轮我交付了什么"，需要回溯整个 recent history。
- 这并非要把 TaskMemory 变成默认 ON（涉及 SQLite 与 settings 默认变更，破坏性较大），而是在第 3 层完全为空时，提供一个轻量的 `<last_deliverable>` 降级填充。

方案：

- `build_turn_message` 在 `working_parts` 为空、且 `session_messages` 中存在最近一条 assistant 消息时，构造短摘要（≤ 600 字）注入 `<working_memory>` → `<last_deliverable>`。
- 摘要使用 `_shorten` + XML escape；trace 注 `source="last_deliverable_fallback",chars=<n>`。
- 当 TaskSummary / TaskIndex 已存在时，仍走原 `<task_summary>` / `<task_index>` 路径，不重复注入。
- 不改 `enable_task_memory` 默认值；保留破坏性变更归 P3 / V3。

已落地：

- `build_turn_message` 在 `working_parts == []` 时，调用新加的 `_extract_last_deliverable(session_messages, max_chars)` 并按需注入 `<last_deliverable>`。
- `_extract_last_deliverable` 跳过 tool / system / user，取最近一条 assistant 文本，去除 XML 危险字符并应用字符上限。
- `working_memory` trace block 在 fallback 命中时记录 `source="last_deliverable_fallback",chars=...`；未命中时仍记 `empty`。

验收：

- 自用默认配置（`enable_task_memory=False`）下，第二轮起 `<working_memory><last_deliverable>` 出现，对应上一轮 assistant 输出的开头 ≤ 600 字。
- 启用 TaskMemory 后，TaskSummary / TaskIndex 优先；fallback 不重复注入。
- 首轮（session_messages 为空）仍输出 `working_memory` trace `empty`。

#### P2-H25 Manifest Miss Visibility

状态：**已完成**。

优先级：P2-H / 低。

原因：

- `enabled_tool_name_set(manifest)` 在 `manifest is None` 时返回 `None`，`filter_tools_by_manifest` 因此跳过过滤，等价于"暴露全部内置工具 + incremental 工具"。
- 这是合理的兼容设计（开发态 skill 无 manifest 也能用），但当生产 / 自用路径配错 skill_id / overlay 目录时，会静默暴露全部工具，没有任何 warn / log；与 V2.5 已经收敛的"manifest 缺失静默暴露"评审项相呼应，但当时只在文档里挂记，没有真正补 warn。

方案：

- 在 `get_agent` 解析完 `manifest = registry.get(eff_skill)` 后，如果 `skill_id` 显式指定但 `manifest is None` 或 manifest 没有任何 `enabled_tools`，记一次 INFO 级日志（不是每轮 warn）：`"manifest miss: skill_id=<x> exposes all platform tools (no enabled_tools allowlist)"`。
- 日志频次控制：缓存 `_manifest_miss_logged: set[str]`，相同 `skill_id` 只 log 一次；进程重启后重置。
- 不修改 `enabled_tool_name_set` 语义，避免破坏依赖 None 兼容路径的旧测试。

已落地：

- `agent_os.agent.factory` 增加模块级 `_MANIFEST_MISS_LOGGED: set[str]` 与一次性 INFO 日志。
- 仅当 `skill_id` 显式传入且 manifest 找不到时记录；默认 skill 兜底路径不算 miss。
- 测试覆盖：传入未注册 skill_id 触发 INFO；同进程内重复传入只 log 一次；传入已注册 skill_id 不记录。

验收：

- 生产 / 自用路径配错 skill_id 时，运维日志能在第一次构造 agent 时看到 manifest miss 提示。
- 开发态默认 skill / 无 manifest 路径不会被噪声日志淹没。
- 不影响现有工具 mask 测试与 manifest 加载语义。

#### V3 / P4 记录项：暂不执行

状态：**记录，不进入当前 P2-H 执行范围**。

- 自动召回缺 per-layer timeout / 真正的并行 + 软降级（V3-record-2）：当前 `MemoryController.retrieve_ordered_context` 是顺序执行；自用阶段先观察，若 Mem0 / Graphiti / Asset 抖动开始影响常规对话，再启动 P3 / P4 工程鲁棒性线。
- 真正的 SubAgent / Todo / Plan / artifact reference 通道（V3-record-3）：P2-H24 的 `<last_deliverable>` 只是 fallback，不是完整 V3 工作记忆；自动 task boundary、复杂任务切分、跨 task 回溯仍归 V3。
- LLM rerank、学习型 auto retrieve、严格 XML schema / parser、统一 sanitizer（V3-record-4）：仅在外部系统 / 多租户 / 商业化部署时启动。
- `enable_task_memory` 默认值是否改为 `True`：本轮不动；改默认涉及 SQLite 路径与运行时副作用，应在 V3 工作记忆主线统一规划。

#### Context V2.6 验证记录

- 定向通过：`python -m pytest tests/core/test_context_builder.py tests/core/test_p2_boundary_negative.py tests/core/test_ordered_context.py tests/core/test_p1_constitutional_output.py -q`。
- 上下文管理矩阵通过：`python -m pytest tests/core/test_context_builder.py tests/core/test_p2_boundary_negative.py tests/core/test_ordered_context.py tests/core/test_web_admin_api.py tests/core/test_cli.py tests/core/test_session_persistence.py tests/core/test_p1_constitutional_output.py -q`。
- 相关文件 `ruff check` 与 `ReadLints` 通过。
- 全量 `python -m pytest tests/core -q` 仍受 `test_ingest_asset_store_minimal_with_allow_llm_off` 网络依赖阻塞（与 V2.5 一致），不属于 V2.6 回归。

### P3：第 3 层复杂迭代

以下问题属于动态工作记忆层的复杂治理，真实有价值，但不应拖慢 1/2/4 层主线：

- SubAgent 隔离：让试错、搜索、长链路探索在独立上下文中完成，只把结论回传主上下文。
- SubAgent 结果 / Todo / 中间产物通道：ContextBuilder 当前没有独立入参承载这些内容；需等 V3 工作记忆设计统一处理。
- Rewind / 回溯：发现错误工具输出或错误记忆污染上下文时，可物理截断或重写会话流。
- Tool result digest / artifact reference：当前折叠 tool output 是防回灌策略；若未来需要继续利用上一轮工具结果，应把大结果存为 artifact，并只把短 digest、source id 和必要摘要进入工作记忆。
- Web TaskMemory 之外的复杂 task boundary：自动识别 task 切换、候选边界、回溯归属与多 task 历史压缩。
- 长任务工作记忆治理：多阶段任务、SubAgent 结果、用户中断和恢复策略。
- 自动召回 timeout / 分层软降级：当外部 Mem0、Graphiti、Asset 或 Hindsight 抖动开始影响常规对话时，再做 per-layer timeout、partial evidence 与 fallback prompt。
- 大规模评测飞轮和真实业务 outcome 闭环。
- 更复杂的历史解析与多模态内容治理：高度畸形的旧 wrapper 修复、非文本 / multimodal message 的语义保真、跨版本 Agno message schema 大迁移。

当前处理策略：**记录但不实现**。这些事项不是本轮 P2-H 的剩余可修 bug；只有当用户明确要求升级、真实长任务暴露明显失败，或现有 ContextBuilder / TaskMemory 简单边界无法支撑时，才进入新一轮计划。

### P4+：平台化与中小规模不涉及的问题

以下能力在自用和中小规模阶段不进入 Context V2 主线：

- 生产鉴权、Web 管理后台、权限平台。
- 多租户强隔离审计平台。
- 计费、配额、成本中心。
- 多实例一致性、分布式锁、统一 watcher。
- Web 多 worker / 多实例 session DB 并发治理：SQLite 只适合本地单进程；多 worker 应使用 PostgreSQL / Redis 等后端并补启动告警与运维文档。
- 外部 skill 包商业化治理。
- XML-like prompt / evidence 的严格 parser、schema validation、统一 sanitizer 与标签白名单平台。
- 大规模不可信召回素材的 prompt injection 防护平台：来源签名、权限分级、可解析 evidence schema、失败降级与审计。
- 完整并发治理与部署平台。

这些能力可以在接口上预留，但不要在本轮提前实现。

## 附录 B：历史推荐路线

### 路线与优先级映射

推荐迭代路线不是独立于 P0/P1/P2 的另一套计划，而是把上文问题清单按工程依赖重新编排后的交付路线。当前映射如下：

| 路线 | 覆盖的优先级项目 | 当前结论 |
| --- | --- | --- |
| Context V2.0 生存线 | P1-1 顶部静止层混入动态信息；P1-2 检索工具输出回灌；P1-3 外部召回确定性入口；P1-4 召回不是指令；P1-5 注意力锚定；P1-6 默认工具收窄；同时吸收 P2-7 / P2-8 / P2-9 与 P2-H9 / P2-H10 的最小实现 | P1 主线已完成，P2 的 trace / history / 入口一致性、动态块顺序与异常输入鲁棒性已有第一版 |
| Context V2.1 格式线 | P1-4 的 usage boundary；P2-2 XML-like evidence；P2-3 superseded 注入治理；P2-4 Graphiti legacy 权威；P2-5 Asset style/source 拆分；P2-H8 prompt 边界最小安全增强 | 已完成；严格 XML parser / schema 校验下放到 P4+ 条件触发清单 |
| Context V2.2 召回质量线 | P1-3 的自动预取稳定性；P2-6 abstain 门；P2-11 Query Planning；P2-12 Contextual Squeezing；P2-13 JIT 按需加载；P2-H2；P2-H6；P2-H7-mini | 第一阶段已完成；Web / CLI Asset 自动预取对齐已完成 |
| Context V2.3 预算线 | P1-2 的回灌成本控制；P2-7 trace；P2-8 summary/history 分工；P2-10 统一 token / char 预算；并复用现有 observability token 粗算；P2-H1；P2-H3-mini；P2-H4；P2-H5 | 字符级第一版与必要 P2-H 已完成；双 history 配置校验已完成 |
| Context V2.4 收敛线 | P2-H11 attention anchor squeezing；P2-H12 static prefix / KV cache hygiene；Web TaskMemory parity；P2-H13 边界补丁收敛 | 已完成；长输入不再在 attention anchor 全文重复，Web 可复用现有 TaskMemory，handoff 生成时间不再污染静态前缀，极小预算 / runtime 标识符注入 / CLI history cap 已补齐回归 |
| Context V2.5 独立评审收敛线 | P2-H14 静态前缀剩余动态摘要 / handoff 层级；P2-H15 attention anchor 语义抽取；P2-H16 recent assistant 长交付物保留；P2-H17 自动召回 / 手动工具去重与 record_* 边界；P2-H18 Web 自动召回客户端复用；V2.5-small 低风险小项；V2.5-small-followup A 类修补；C-test-hardening 测试 / 压力复核；后续只读复查补测 | 已完成；英文 / 零宽关键词、子串误匹配、auto retrieve 关闭态 trace、异常 external result、trace 单行清洗、handbook version 静态层、空 ordered context shell、Graphiti 空白返回、runtime context `None` 标识符、外部 adapter 异常软降级、hard budget 极端语义与 Agno history 逃生配置均已有回归；不重开 V2.0-V2.4，也不引入 P3/P4 复杂治理 |
| Context V2.6 上下文工程独立评审第二轮 | P2-H19 静态前缀跨 entrypoint 漂移（Web 演示提示下沉到 attention_anchor）；P2-H20 自动召回与 `retrieve_ordered_context` 工具真正互斥；P2-H21 runtime_context 秒级时间分钟化；P2-H22 attention_anchor `<restated_goal>` 目标复述；P2-H23 tool output 分级折叠（短输出保留原文）；P2-H24 working_memory 默认 `<last_deliverable>` 降级填充；P2-H25 manifest 未命中一次性 INFO 日志 | 已完成；不再重开"是否要把演示提示放静态层 / 是否要每秒更新 runtime_context / 是否要做完整 SubAgent 工作记忆 / 是否把 enable_task_memory 改默认 ON"；自动召回 per-layer timeout、LLM rerank、SubAgent 结果通道、自动 task boundary 仍归 V3 / P4 |

因此，当前规划中的 P0 仍为空；V2.0-V2.5 已覆盖全部原始 P1、P2-1~P2-13、已验收 P2-H、A 类小修、C 类基础测试与本轮独立评审新增收敛项。剩余复杂事项归入 **V3 复杂工作记忆线** 或 **P4+ 平台线**，例如 SubAgent 隔离、Rewind、复杂 task boundary、自动召回 timeout、多 worker session DB 并发治理、严格 XML/schema/sanitizer 与平台化 prompt-injection 防护。

### Context V2.0：生存线

状态：**已完成（当前阶段可验收）**。

目标：解决最容易导致上下文失控的问题。

范围：

1. 新增统一 ContextBuilder 的最小实现。
2. 静态 instructions 只保留稳定系统规则、skill contract、工具使用总则。
3. 将 ephemeral metadata、retrieved context、current anchor 移出静态 instructions。
4. 验证并控制 `retrieve_ordered_context` 输出回灌历史的风险；必要时关闭 Agno 自动 history 注入，改由 ContextBuilder 注入清洗后的 history。
5. 增加注意力锚定层。
6. 加强“召回数据不是指令”的规则。

验收：

- 同一 session 多轮对话中，大块召回内容不会反复回灌。
- 检索工具的历史回放只保留短摘要、占位符或压缩 evidence，不回放完整召回原文。
- 每轮上下文能区分 static / retrieval / working memory / current anchor。
- 当前用户指令在结构上位于最后，并有明确目标锚。
- 默认工具集可被 skill manifest 收窄。

已落地：

- `ContextBuilder` 作为每轮动态上下文入口，输出 `runtime_context`、`working_memory`、`external_recall`、`recent_history`、`attention_anchor` 与最终 `current_user_message`。
- `get_agent` 在启用 ContextBuilder 与自管 history 时关闭 Agno 原始 `add_history_to_context`，避免大块工具输出反复回灌。
- `clean_history_messages` 会解包已包装的用户消息、折叠 tool output，并限制单条 history 内容长度。
- `external_recall` 明确标注 `evidence_only`，提示召回内容不得覆盖 system / developer / 当前用户指令。
- `attention_anchor` 固定置于当前轮上下文后段，当前用户请求在结构上位于最后。
- skill manifest 已支持默认工具集收窄，`enabled_tools=[]` 表示不暴露工具。

仍需关注 / 后续补充：

- 需要在真实多轮 CLI / Web 会话中继续观察 Agno DB 返回消息结构，确认不同版本 Agno 下 tool output 折叠逻辑不失效。
- Web 已可在 `AGENT_OS_ENABLE_TASK_MEMORY=1` 时复用现有 TaskMemory；复杂 task boundary、SubAgent 与 Rewind 仍归入 V3。

### Context V2.1：格式线

状态：**已完成（当前阶段可验收）**。

目标：提高模型对上下文边界的理解。

范围：

1. 用 XML/XML-like 格式输出上下文块。
2. Asset Store 拆分 style_reference 与 source_material。
3. 每条召回内容增加 usage rule。
4. 召回输出增加 evidence metadata。

验收：

- style reference 不再与 background material 混在同一块。
- Hindsight、Graphiti、Asset、Mem0 均有明确 source 与 usage rule。
- 结构化上下文仍能被现有 Agent 消费。

已落地：

- `retrieve_ordered_context` 输出升级为 XML-like evidence bundle，当前版本为 `version="2.2"`。
- Mem0、Hindsight、Graphiti、Asset 均带 `source`、`authority`、`usage_rule`、`relevance` 等元数据。
- Asset Store 已拆分为 `<style_references>` 与 `<source_materials>`，分别约束为 `style_only` 与 `source_material_only`。
- Hindsight 默认隐藏 superseded 旧经验；debug 模式可显示并保留 `superseded` 标记。
- Graphiti legacy / fallback 会标记为低权威来源，如 `legacy_compat` 或 `fallback_knowledge`。

仍需关注 / 后续补充：

- 需要实测不同模型对 XML-like evidence bundle 的遵循差异，决定是否进一步收紧标签命名或增加更硬的 schema。
- 目前是 XML-like 字符串，不是严格 XML parser 校验；若后续要供机器链路解析，可补 XML/schema 校验测试。
- XML parser / schema 校验暂不放入近期 P2；仅在外部系统需要机器解析 evidence bundle 时触发。

### Context V2.2：召回质量线

状态：**已完成第一阶段（自用可落地版）**。

必要 P2-H：**P2-H2 / P2-H7-mini 已完成**。

目标：减少弱相关召回和无效注入。

范围：

1. Query Planning。
2. 统一 relevance / abstain 门。
3. Contextual squeezing 默认用于长 Asset / Graphiti 候选。
4. 召回 trace。

验收：

- 同一用户请求可看到分层 query。
- 弱相关内容进入 trace 但不进入 prompt。
- 长素材默认压缩为任务相关摘要。

已落地：

- 新增 `plan_retrieval_subqueries`，生成 `profile_query`、`lesson_query`、`knowledge_query`、`style_query`、`material_query`；ordered context 可输出 `<query_plan>`。
- Mem0、Graphiti、Hindsight、Asset 均接入低相关 abstain 逻辑；Graphiti 低相关正文不进入 prompt，只输出 `<abstained />` 与 `abstained_count`。
- Asset 检索按 `style_reference` / `source_material` 分层 query；默认不启用 token overlap 二次过滤，避免中英特征不交时误杀，保留可选 L2 / overlap 阈值。
- Hindsight 检索保留 raw query，以避免扩写 query 把英文教训挤出 Top-K；行级 overlap 默认关闭但可配置。
- `format_asset_hits_for_agent(include_raw=True)` 已有节选上限 `AGENT_OS_ASSET_INCLUDE_RAW_MAX_CHARS`，避免显式工具调用返回过长原文。
- JIT hint 已作为设计预留注入：提示 Graphiti / Asset 可按需通过工具再展开完整内容。

仍需关注 / 后续补充：

- 当前 Query Planning 是确定性模板扩写，不是 LLM planner 或学习型 planner；复杂多意图请求可能仍需更细粒度拆解。
- 当前 abstain trace 以 evidence metadata / ContextBuilder trace 为主，没有持久化完整“被拒候选正文”；若需要离线调参，可增加 debug-only trace sink。
- Contextual squeezing 目前依赖 Hindsight / Asset synthesis 开关和字符预算；Graphiti 尚未做 query-relevant 摘要层，长 Graphiti 正文主要靠预算截断和 JIT 设计预留控制。
- 后续可增加 rerank / evaluator fixture，验证 abstain 阈值不会误杀关键记忆。

### Context V2.3：预算线

状态：**已完成字符级第一版（当前阶段可验收）**。

必要 P2-H：**P2-H1 / P2-H3-mini / P2-H4 已完成**。

目标：让上下文成本与窗口占用可控。

范围：

1. 统一字符预算。
2. 分层预算策略。
3. 超预算压缩和截断规则。
4. token 粗算进入 observability。

验收：

- 每轮 prompt 结构和长度可解释。
- 超预算时优先保留 current anchor 和高权威规则。
- 大块 Asset / Graphiti / history 不会挤掉当前任务。

已落地：

- 新增 `ContextCharBudget`，默认总预算 `AGENT_OS_CONTEXT_MAX_CHARS=24000`，并按比例分配 working memory、external recall、recent history。
- `ContextBuilder` 会对 `working_memory`、`external_recall`、`recent_history` 做字符级截断，并注入 `<char_budget_truncated ... />` 标记。
- `attention_anchor` 与最终 `current_user_message` 不被块级预算截断，确保当前任务不会被召回或历史挤掉。
- `ContextTrace` 记录每块字符数、来源、是否注入、截断原因和总预算状态；`AGENT_OS_CONTEXT_TRACE_LOG=1` 时写入 `AGENT_OS_CONTEXT_TRACE` 日志。
- `AGENT_OS_SESSION_HISTORY_CAP_WHEN_TASK_SUMMARY` 在存在 TaskSummary 时收紧 recent history 条数，减少摘要与原文重复。
- 运行层已有 `AGENT_OS_OBS` token 粗算日志，可与 ContextTrace 结合排查上下文成本。

仍需关注 / 后续补充：

- 当前预算是字符级，不是 tokenizer 级；不同模型的 token / char 比例仍需实测校准。
- 目前分层预算比例固定在代码中；后续可按 skill / model / entrypoint 配置化。
- 当前超预算策略是截断，不是对 Graphiti / history 做二次摘要；更优策略可放入 V2.x hardening 或 V3。
- `context_budget` trace 记录 total over/within budget；默认不会物理截断最终整体消息。
- `AGENT_OS_CONTEXT_HARD_BUDGET=1` 时会启用保守硬总预算：整块省略 `recent_history`、`external_recall`、`working_memory`，但仍不裁剪 `attention_anchor` 和最终 `current_user_message`。
- 若需要限制超长当前用户输入，应在 CLI / Web / API 入口层设置 body 或字段长度上限；不要把当前用户消息放入硬预算裁切范围。

### Context V3：复杂工作记忆线

目标：处理复杂长任务、试错隔离和上下文回溯。

范围：

- SubAgent 隔离。
- Rewind / 回溯。
- 复杂 task boundary。
- 长任务工作记忆治理。
- 真实业务 outcome 闭环。

### Context V4+：平台线

目标：面向多用户、多 skill、大量工具和商业化部署。

范围：

- 权限与鉴权平台。
- 并发治理与多实例一致性。
- 成本中心、配额和审计。
- 外部 skill 包商业化治理。
- XML-like prompt / evidence 的严格 parser、schema validation、统一 sanitizer 与标签白名单平台。

自用和中小规模阶段不进入本轮计划。

## 附录 C：历史自审与实测清单

### 问题是否真实

本文件列出的 P1/P2 问题在提出时均能在当时代码中找到对应实现依据。经过本轮 Context V2 研发后，以下问题已经有对应修复或缓解：

- 静态 instructions 与每轮动态上下文已通过 `ContextBuilder` 分离。
- `retrieve_ordered_context` 可作为工具调用，也可由 ContextBuilder 自动预取后包进 `external_recall`。
- Agno 原始 history 注入在 ContextBuilder 自管模式下关闭，改为清洗后的 `recent_history`。
- Asset ordered context 已硬拆 `style_reference` 与 `source_material`。
- manifest `enabled_tools=[]` 已可表达“不暴露工具”。
- 当前请求已进入 `attention_anchor` 与最终 `current_user_message`。
- TaskSummary 与 recent history 已有分工、覆盖范围 trace 和 summary 存在时的 history cap。
- 二次审查后，Web 启用 ContextBuilder 时的 recent history 已优先来自 Agno 持久化 session DB，避免跨进程 / 重启后只依赖 `_transcripts`。
- `/chat`、`/ingest`、`/api/memory/ingest` 与 CLI 交互路径已补充空白、超长、异常字符、畸形 JSON、非法 target / kind / `mem_kind` 等负向测试。
- 三轮硬化后，`ContextBuilder`、TaskMemory 与 Query Planning 已补齐非字符串 / `None` / bytes / 畸形 history shape / XML-like 注入等异常输入的最小鲁棒性；外层 prompt 标签计数与动态块顺序有回归测试和压力脚本验证。

仍需继续实测的是真实模型遵循度、不同 Agno 版本的 session message 结构差异、长会话下预算比例是否需要按 skill / model 调参，以及 P2-H5 / P2-H6 / P2-H10 的最小 hardening 在真实 Web / CLI 会话中是否足够。

### 优先级是否合理

在自用背景下，P0 暂不设项是合理的。原因是当前系统能跑通，数据规模和工具数量还没有达到必然失控的程度。

P1 聚焦“质量与注意力会稳定受损”的事项，符合本轮 Context V2 的目标：

- 静态层与动态层分离。
- 回灌风险控制。
- 外部召回确定性策略。
- 防注入边界。
- 当前目标锚定。
- 默认工具收窄。

P2 统一收纳 1/2/4 层 SOTA 增强和第 3 层简单迭代，并按研发难度由低到高推进：工具描述压缩、XML 证据包、legacy / superseded 标记、Asset 类型隔离、相关性门控、trace、history/summary 分工、入口一致性、预算、Query Planning、squeezing、JIT，以及 V2.x hardening 中的配置互斥、入口能力对齐、prompt 边界最小安全增强、动态块顺序对齐和异常输入鲁棒性收敛。

P3 聚焦第 3 层复杂工作记忆能力，例如 SubAgent 隔离、Rewind、复杂 task boundary 与长任务治理。

P4+ 明确排除平台化、鉴权、审计平台、商业化治理、严格 XML schema / parser 与统一 sanitizer 平台，符合当前“自用优先”的约束，也与 Memory V2 文档中“不把主线扩展成平台治理系统”的边界一致。

### 需要实测确认的点

以下事项在代码已落地后仍应做实测或后续 hardening：

1. 真实 CLI / Web 多轮会话中，Agno DB 返回的 message 结构是否始终能被 `clean_history_messages` 正确折叠。
2. 不同模型对 XML/XML-like 上下文与 Markdown 上下文的实际差异，尤其是 `usage_rule` 与 `attention_anchor` 的遵循度。
3. 自动预取 `retrieve_ordered_context` 的关键词触发条件是否过宽或过窄；必要时按 skill 增加触发策略。
4. 字符预算与真实 token 使用的比例关系；必要时接 tokenizer 或按模型维护经验系数。
5. Abstain 阈值对真实任务的误杀率；必要时增加 rerank / debug trace sink。
6. `enable_context_builder=True` 与 `context_self_managed_history=False` 的非默认组合默认已 suppress Agno history；仍需观察是否有人需要显式 override。
7. Web 入口已与 CLI 共享 ContextBuilder，自动预取也已跟随 Asset Store settings；仍需真实 Web 会话验证 Asset 命中质量。
8. Web 尚未补齐与 CLI 对等的完整 TaskMemory 流程；该项属于 V3 复杂工作记忆线，不是当前 P2 blocker。
9. 用户输入或外部文本包含 XML-like 闭合标签时，当前 prompt 边界已有 P2-H8 最小转义 / literal 保护；严格 parser / schema 下放 P4+。
10. 非字符串、bytes、`None`、畸形 history shape 等异常输入已有 P2-H10 最小鲁棒性保护；若未来发现新类型，补具体 fixture，不要重开整条 Context V2 主线。
11. A 类小项的 deterministic fixture 已补齐基础覆盖：`handbook_version` / manifest metadata 对静态 instructions 稳定性的影响、全 abstain / 空召回时 external recall 的渲染语义、Web / CLI 工具 mask 差异的预期边界、attention anchor 压缩后语义是否仍完整。当前状态：**已完成基础覆盖**；后续若新增 A 类小项，应继续补同粒度 fixture。
12. B 类 trade-off 的回归 fixture 已补充基础边界：tool output 折叠、防历史回灌、显式 Agno history 逃生配置、hard budget 仍可能 over budget 的语义均已有测试。当前状态：**基础语义已固定**；未来若引入 artifact reference，需验证不会重新造成 retrieve output 历史回灌。
13. 模型遵循度实测：同一任务在不同模型下对 `usage_rule=evidence_only`、`tool_boundary`、`attention_anchor` 抽取约束的遵循差异。当前状态：**结构性 prompt 边界 fixture 已补齐，真实 LLM 遵循度仍需人工 / 小型评测确认**；不要把该项误判为当前单测缺口。
14. 外部依赖鲁棒性实测：Mem0 / Graphiti / Asset / Hindsight 任一层异常或空结果时，ordered context 已有软降级测试，Graphiti 空白返回已固定为 `empty`。当前状态：**异常 / 空结果基础覆盖已完成**；慢响应 / timeout 的 per-layer 时间治理仍属于 V3/P4，只有真实使用中影响常规对话时再升级。

### 文档风险

本文最初是迭代设计文档，现在同时承担跨设备 / 跨 Agent 协作状态记录。若后续实现与本文不同，应以代码和测试为准，并同步更新本文的“解决状态 / 已落地 / 验证 / 协作备注”，避免已解决事项被重复打开。
