# Stage Execution Plan

本文记录 `agent-os-runtime` 按阶段推进 Claude Code 参考架构规划的当前战役与完成历史。

顶层原则见 [CLAUDE_CODE_REFERENCE_ROADMAP.md](CLAUDE_CODE_REFERENCE_ROADMAP.md)；跨阶段 Sprint/DoD 拆解见 [SPRINT_IMPLEMENTATION_ROADMAP.md](SPRINT_IMPLEMENTATION_ROADMAP.md)。本文只记录可频繁更新的 Stage 战役细节，避免顶层 roadmap 被日常研发噪声污染。

## 文档使用规则

- 顶层方向、模块边界、阶段定义写入 `CLAUDE_CODE_REFERENCE_ROADMAP.md`。
- 当前阶段的任务拆解、验收口径、完成历史写入本文。
- 当某个阶段超过一份文档可维护范围时，再拆 `docs/stages/STAGE_<N>_*.md`。
- 不在本文记录平台化多租户、计费、复杂鉴权等远期细节，除非它们进入当前阶段。

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

### 完成历史

| 日期 | 项目 | 结果 |
| --- | --- | --- |
| 2026-04-27 | Context Diagnostics v0 | 已完成：新增 diagnostics 模块、CLI `context-diagnose`、Web trace 接入与针对性测试 |
