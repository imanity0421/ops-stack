# Stage Execution Plan

> **Deprecated / Historical Archive**
>
> 本文已废弃并归档，仅保留作历史参考。当前架构权威见 [../ARCHITECTURE.md](../ARCHITECTURE.md)；Stage 2 battle 顺序与完成度见 [../OPEN_DECISIONS.md](../OPEN_DECISIONS.md) D1；已完成交付见 [../CHANGELOG.md](../CHANGELOG.md)。
>
> 新研发不要继续编辑本文，也不要把本文作为 active stage plan。

本文记录 `agent-os-runtime` 按阶段推进 Claude Code 参考架构规划的当前战役与完成历史。

顶层原则见 [CLAUDE_CODE_REFERENCE_ROADMAP.md](CLAUDE_CODE_REFERENCE_ROADMAP.md)；跨阶段 Sprint/DoD 拆解见 [SPRINT_IMPLEMENTATION_ROADMAP.md](SPRINT_IMPLEMENTATION_ROADMAP.md)。效果优先、避免过度复杂的新阶段规划草案见 [EFFECT_FIRST_STAGE_PLAN.md](EFFECT_FIRST_STAGE_PLAN.md)，该草案在反复校验前不替代顶层 roadmap。本文只记录可频繁更新的 Stage 战役细节，避免顶层 roadmap 被日常研发噪声污染。

## 文档使用规则

- 顶层方向、模块边界、阶段定义写入 `CLAUDE_CODE_REFERENCE_ROADMAP.md`。
- 当前阶段的任务拆解、验收口径、完成历史写入本文。
- Claude Code 源码参考导航写入 `CLAUDE_CODE_REFERENCE_INDEX.md`。
- 当某个阶段超过一份文档可维护范围时，再拆 `docs/stages/STAGE_<N>_*.md`。
- 不在本文记录平台化多租户、计费、复杂鉴权等远期细节，除非它们进入当前阶段。

## 研发前置流程

每个 Stage / battle 开工前，先判断本次能力是否属于“Claude Code 可参考优先”的范围，例如 context runtime、compact、tool result folding、slash command、session resume、subagent 等。

如果存在对应参考，先按 [CLAUDE_CODE_REFERENCE_INDEX.md](CLAUDE_CODE_REFERENCE_INDEX.md) 做一次 Reference Check，再进入设计和编码：

注意：Reference Check 的目标不是照抄 Claude Code，而是避免纯硬造。Claude Code 的 coding harness、Anthropic message schema、CLI/Ink UI、权限模型等经常不能直接适配 Agno 架构；每次研发都必须分析“可迁移机制”和“不可照搬部分”，再形成适合 `agent-os-runtime` 的实现边界。

```markdown
### Claude Code Reference Check

- 本次能力：
- 是否有 Claude Code 参考：是 / 否
- 已阅读源码：
  - `claude_code/cc-recovered-main/src/...`
- 可借鉴机制：
- 不应照搬的部分：
- 选择性借鉴后的适配方案：
- Agent OS 实现边界：
```

如果没有对应参考，不强行对标 Claude Code；可以直接依据 `agent-os-runtime` 现有 Agno 架构、Memory V2、Context V2 和业务场景需求推进。

## Stage 1：Context V2 基建期

目标：系统不爆、不乱、不污染。完成后应具备“上下文可观测、可预算、可防爆”的 SOTA 感。

### 当前战役：Context Diagnostics v0

范围：

1. 基于现有 `ContextTrace` 与 `ContextBundle` 生成 `/context` 所需的稳定数据结构。
2. 输出 runtime context、external recall、working memory、recent history、attention anchor、current user message 的 chars / 注入状态 / 来源 / 预算状态。
3. 暴露 CLI 开发命令，用于不调用模型的上下文预检。
4. Web `/chat` trace 中带出同一份 diagnostics，便于前端调试面板后续接入。
5. 不做 compact、不做 artifact store、不做 model-driven context management。

验收：

- 单测覆盖 diagnostics 数据结构、Markdown 输出、预算状态。
- CLI 可输出 JSON 或 Markdown。
- Web trace 在启用 ContextBuilder 且 `include_trace=true` 时包含 `context_diagnostics`。
- 现有 ContextBuilder / CLI / Web 回归测试通过。

### 当前战役：Context Budget Guard v0

Claude Code Reference Check：

- 本次能力：`/context` 预算预警、阻断态识别、研发前上下文预检。
- 是否有 Claude Code 参考：是。
- 已阅读源码：
  - `claude_code/cc-recovered-main/src/services/compact/autoCompact.ts`
  - `claude_code/cc-recovered-main/src/utils/toolResultStorage.ts`
  - `claude_code/cc-recovered-main/src/constants/toolLimits.ts`
  - `claude_code/cc-recovered-main/src/utils/messages.ts`
- 可借鉴机制：warning / danger / blocking 阈值分层；大结果先预算、再压缩或外置；API-bound 消息进入模型前应有统一预检。
- 不应照搬的部分：不在 Stage 1 引入 Claude Code 的 compact 执行、React Ink UI、Anthropic `Message[]` schema 或 coding tool 特定处理。
- 选择性借鉴后的适配方案：只借鉴阈值分层和预检思想，落到 `ContextDiagnostics` 的预算 guard 与 CLI 退出码；暂不改变 Agno `Agent.run` 消息循环。
- Agent OS 实现边界：保留 `ContextBuilder` / `ContextBundle` / `ContextTrace` 分层，在 diagnostics 层输出预算 guard；CLI 增加可选预检退出码，不调用模型，不做 compact，不做 artifact store。

范围：

1. 在 `/context` diagnostics 中输出 warning / danger / over_budget / current user high ratio 等预算 guard 信号。
2. 对当前用户消息占比单独提示，因为 ContextBuilder 永不裁切本轮用户请求。
3. CLI `context-diagnose` 支持作为研发前预检门禁使用。
4. 不做自动 compact，不做长结果持久化，只为下一战役提供可观测触发条件。

验收：

- 单测覆盖 budget guard 的结构化字段和建议项。
- CLI 在达到指定预算级别时可返回非零退出码。
- 原有 diagnostics JSON / Markdown 输出继续可用。

### 当前战役：Tool Result History Budget v0

Claude Code Reference Check：

- 本次能力：历史工具结果聚合预算，防止多个工具结果在下一轮 recent history 中合计挤爆上下文。
- 是否有 Claude Code 参考：是。
- 已阅读源码：
  - `claude_code/cc-recovered-main/src/utils/toolResultStorage.ts`
  - `claude_code/cc-recovered-main/src/constants/toolLimits.ts`
  - `claude_code/cc-recovered-main/src/utils/messages.ts`
- 可借鉴机制：单个工具结果和单条 API user message 的工具结果聚合预算分层；预算决策应稳定、可追踪；多个并行工具结果合并进模型输入前必须做聚合约束。
- 不应照搬的部分：Stage 1 不引入 Claude Code 的磁盘 persisted-output、content replacement transcript、prompt cache 稳定策略或 Anthropic `tool_result` schema。
- 选择性借鉴后的适配方案：在 `ContextBuilder` 的 history cleaning 边界实现历史工具结果聚合预算，优先保留更新的短工具结果，预算耗尽时省略更旧工具结果，并把 folded / omitted / kept / original 写入 `ContextTrace`。
- Agent OS 实现边界：只处理 ContextBuilder-managed recent history；不改变 Agno 原始 tool call 存储，不做 artifact store，不做可重放工具结果引用。

范围：

1. `clean_history_messages` 支持历史工具结果合计预算。
2. 新增结构化清洗报告，供 `ContextTrace` 暴露 tool history budget 统计。
3. 配置项控制聚合预算，默认开启，允许设为 0 关闭。
4. 保持短工具结果仍可进入上下文，长工具结果仍先按单条上限折叠。

验收：

- 单测覆盖多个工具结果合计超过预算时的省略行为。
- ContextBuilder trace 输出 tool_total_budget / tool_kept / tool_original / tool_folded / tool_omitted。
- CLI / Web 入口均沿用同一配置项。

### 当前战役：Prompt Too Long Self-Heal v0

Claude Code Reference Check：

- 本次能力：prompt-too-long / 超预算自救 v0。
- 是否有 Claude Code 参考：是。
- 已阅读源码：
  - `claude_code/cc-recovered-main/src/services/compact/compact.ts`
  - `claude_code/cc-recovered-main/src/services/api/errors.ts`
  - `claude_code/cc-recovered-main/src/query.ts`
- 可借鉴机制：在 prompt-too-long 错误直接暴露前，先做一次可恢复处理；最保守的 fallback 是丢弃最旧、低优先级上下文来恢复运行；自救失败后再暴露错误，避免无限重试。
- 不应照搬的部分：Stage 1 不做 reactive compact、context collapse、API error withholding、streaming query loop retry，也不解析 Anthropic 413 错误作为主路径。
- 选择性借鉴后的适配方案：把自救前移到 `ContextBuilder` pre-run 阶段；当最终 prompt 超过配置总预算时，自动执行确定性低优先级块省略，顺序为 `recent_history -> external_recall -> working_memory`，永远不裁剪当前用户消息。
- Agent OS 实现边界：只做确定性 self-heal，不调用模型、不摘要、不 compact；CLI / Web 沿用同一开关，trace 记录 `budget_self_heal` 和 `hard_budget_trim`。

范围：

1. ContextBuilder 默认开启超预算自救。
2. 自救只移除低优先级上下文块，不裁剪 current user message。
3. 新增配置项允许关闭自救，用于 diagnostics 或边界测试。
4. trace 明确记录自救前后字符数与裁切顺序。

验收：

- 单测覆盖默认自救后 prompt 回到预算内。
- 单测覆盖关闭自救时仍可观察 over budget。
- CLI / Web 均接入同一配置项。

### 当前战役：Stage 1 Final Acceptance & Smoke

范围：

1. 固化 Stage 1 最终完成定义，避免继续向 Stage 2 / Stage 3 蔓延。
2. 用 CLI `context-diagnose` smoke 覆盖 Stage 1 的真实入口行为。
3. 明确冻结项：compact、artifact store、SubAgent、model-driven context management 不进入 Stage 1。

最终 DoD：

- `/context` 可输出结构化 diagnostics 与 Markdown diagnostics。
- diagnostics 包含块级 chars / 注入状态 / 来源 / note / prompt 占比。
- budget guard 可判断 ok / warning / danger / over_budget，并输出当前用户消息高占比提示。
- ContextBuilder 默认支持超预算 self-heal，按 `recent_history -> external_recall -> working_memory` 低优先级裁切。
- current user message 永远不被裁切；如果当前用户消息自身过大，diagnostics 必须明确提示。
- recent history 中历史工具结果有单条预算与聚合预算，trace 记录 tool_original / tool_kept / tool_folded / tool_omitted。
- CLI / Web 均接入同一套 ContextBuilder、diagnostics、budget 与 self-heal 配置。
- Stage 1 所有能力都有针对性测试或 smoke 测试。

Stage 1 冻结到后续阶段：

- manual compact、compact boundary、post-compact rehydration、auto compact v1 进入 Stage 2。
- artifact store v1、artifact_id + digest + usage_rule 进入 Stage 2。
- SubAgent sandbox、Research / Reviewer agent 进入 Stage 3。
- 模型自驱 context management、Rewind / Clear / Todo tool 进入 Stage 3 或更后。

验收：

- CLI smoke 覆盖 self-heal 后不会调用模型也能给出预算诊断。
- CLI smoke 覆盖工具结果聚合预算的 trace 统计。
- targeted pytest 与 ruff 通过。

### 完成历史

| 日期 | 项目 | 结果 |
| --- | --- | --- |
| 2026-04-27 | Context Diagnostics v0 | 已完成：新增 diagnostics 模块、CLI `context-diagnose`、Web trace 接入与针对性测试 |
| 2026-04-27 | Context Budget Guard v0 | 已完成：diagnostics 输出预算 guard、当前用户消息高占比提示，CLI `--fail-on-budget` 支持预检退出码 |
| 2026-04-27 | Tool Result History Budget v0 | 已完成：history 工具结果聚合预算、清洗报告与 ContextTrace 统计，CLI / Web 配置接入 |
| 2026-04-27 | Prompt Too Long Self-Heal v0 | 已完成：ContextBuilder 超预算自动低优先级裁切，自救 trace 与 CLI / Web 配置接入 |
| 2026-04-27 | Stage 1 Final Acceptance & Smoke | 已完成：Stage 1 最终 DoD、冻结边界与 CLI smoke 验收 |
