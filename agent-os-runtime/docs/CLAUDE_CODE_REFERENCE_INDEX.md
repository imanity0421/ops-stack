# Claude Code Reference Index

本文是 `agent-os-runtime` 参考 `claude_code/cc-recovered-main` 研发时的源码导航。

它不是 Claude Code 架构复刻说明，也不是全量源码解读。它只回答一个问题：当某个 Agent OS 能力明确借鉴 Claude Code Harness 时，agent 开工前应该先读哪里，重点看什么，哪些地方不要照搬。

核心原则：**不纯硬造，也不无脑抄 Claude Code**。Claude Code 是参考对象，不是目标架构。每次借鉴都必须经过分析和取舍，把可迁移的 Harness 机制转译成更适合当前 Agno-based `agent-os` 的实现方案。

## 使用方式

每个 Stage / battle 开工前，如果任务属于“Claude Code 可参考优先”的范围，先做一次 Reference Check：

1. 在本文找到对应能力域。
2. 阅读列出的 Claude Code 源码文件，必要时再沿 import / call site 扩展。
3. 在实现前写明：
   - 已阅读的 Claude Code 文件；
   - 可借鉴的机制；
   - 因 `agent-os` 基于 Agno 架构而不能照搬的部分；
   - 选择性借鉴后的适配方案；
   - 本次落到 `agent-os-runtime` 的实现边界。
4. 如果本文没有对应条目，先用代码搜索定位，再把稳定结论补回本文。

## 核心参考域

### Context Runtime / Diagnostics / Token Budget

优先参考：

- `claude_code/cc-recovered-main/src/context.ts`
- `claude_code/cc-recovered-main/src/commands/context/context.tsx`
- `claude_code/cc-recovered-main/src/commands/context/context-noninteractive.ts`
- `claude_code/cc-recovered-main/src/utils/analyzeContext.ts`
- `claude_code/cc-recovered-main/src/utils/contextAnalysis.ts`
- `claude_code/cc-recovered-main/src/components/ContextVisualization.tsx`
- `claude_code/cc-recovered-main/src/services/compact/autoCompact.ts`

关注点：

- 如何收集系统 / 用户 / 环境上下文；
- `/context` 类命令如何把上下文使用量暴露给操作者；
- token window、warning threshold、auto compact threshold 的计算方式；
- diagnostics 与真实 runtime 消息管线之间的边界。

不要照搬：

- Claude Code 面向 CLI coding harness 的 UI / terminal 细节；
- 直接以 `Message[]` 作为唯一上下文真相。`agent-os` 应保留 `ContextBuilder`、`ContextBundle`、`ContextTrace` 的分层结构。

### Compact / Prompt Too Long Recovery

优先参考：

- `claude_code/cc-recovered-main/src/services/compact/compact.ts`
- `claude_code/cc-recovered-main/src/services/compact/autoCompact.ts`
- `claude_code/cc-recovered-main/src/services/compact/microCompact.ts`
- `claude_code/cc-recovered-main/src/services/compact/sessionMemoryCompact.ts`
- `claude_code/cc-recovered-main/src/services/compact/postCompactCleanup.ts`
- `claude_code/cc-recovered-main/src/commands/compact/compact.ts`
- `claude_code/cc-recovered-main/src/query.ts`

关注点：

- 手动 compact、自动 compact、prompt-too-long reactive compact 的触发边界；
- compact 后如何替换旧历史，而不是追加一层噪声；
- compact 失败后的 circuit breaker / retry 边界；
- compact 摘要如何保留任务目标、关键事实、未完成事项和工具结果引用。

不要照搬：

- 直接把 Claude Code 的 coding transcript 摘要模板套到商业 / 运营 / 写作任务；
- 在 Stage 1 做完整 compact。Stage 1 只做可观测、预算和防爆基础。

### Message Normalization / History Cleaning

优先参考：

- `claude_code/cc-recovered-main/src/utils/messages.ts`
- `claude_code/cc-recovered-main/src/query.ts`
- `claude_code/cc-recovered-main/src/utils/analyzeContext.ts`

关注点：

- `normalizeMessagesForAPI` 如何处理消息进入模型前的结构；
- tool use / tool result 配对、过滤、截断的前置约束；
- prompt-too-long 时哪些历史可以被截断，哪些必须保留。

不要照搬：

- Claude Code 的 Anthropic message schema 细节；
- 把历史清理逻辑散落在多个入口。`agent-os` 应尽量集中在 ContextBuilder / history cleaner 边界内。

### Tool Result Folding / Artifact Reference

优先参考：

- `claude_code/cc-recovered-main/src/utils/toolResultStorage.ts`
- `claude_code/cc-recovered-main/src/constants/toolLimits.ts`
- `claude_code/cc-recovered-main/src/services/toolUseSummary/toolUseSummaryGenerator.ts`
- `claude_code/cc-recovered-main/src/utils/groupToolUses.ts`
- `claude_code/cc-recovered-main/src/components/messages/UserToolResultMessage/UserToolResultMessage.tsx`
- `claude_code/cc-recovered-main/src/components/messages/AssistantToolUseMessage.tsx`

关注点：

- 长工具结果如何从上下文中外置，只保留摘要 / 引用 / 用途；
- 每条消息、每类工具结果的预算边界；
- 工具结果展示给人和注入给模型之间的差异。

不要照搬：

- Claude Code 针对 shell / file edit / coding 工具的具体 UI 呈现；
- 在没有 artifact store 边界前，把长结果折叠写成临时字符串替换。

### Slash Commands / Operator Control Plane

优先参考：

- `claude_code/cc-recovered-main/src/commands.ts`
- `claude_code/cc-recovered-main/src/types/command.ts`
- `claude_code/cc-recovered-main/src/utils/processUserInput/processSlashCommand.tsx`
- `claude_code/cc-recovered-main/src/utils/slashCommandParsing.ts`
- `claude_code/cc-recovered-main/src/utils/commandLifecycle.ts`
- `claude_code/cc-recovered-main/src/commands/compact/compact.ts`
- `claude_code/cc-recovered-main/src/commands/context/context.tsx`

关注点：

- 命令注册、解析、执行、结果回灌之间的边界；
- operator command 与模型工具调用的区别；
- `/compact`、`/context` 这类命令如何成为人工可控的上下文治理入口。

不要照搬：

- Claude Code 的 React Ink UI 层；
- 过早建设完整 command platform。Stage 1/2 只保留能改善效果和调试体验的少数命令。

### SubAgent / Isolated Worker

优先参考：

- `claude_code/cc-recovered-main/src/tools/AgentTool/AgentTool.tsx`
- `claude_code/cc-recovered-main/src/tools/AgentTool/runAgent.ts`
- `claude_code/cc-recovered-main/src/tools/AgentTool/forkSubagent.ts`
- `claude_code/cc-recovered-main/src/tools/AgentTool/resumeAgent.ts`
- `claude_code/cc-recovered-main/src/tools/AgentTool/agentToolUtils.ts`
- `claude_code/cc-recovered-main/src/tools/AgentTool/agentMemory.ts`
- `claude_code/cc-recovered-main/src/tools/AgentTool/agentMemorySnapshot.ts`
- `claude_code/cc-recovered-main/src/tools/shared/spawnMultiAgent.ts`
- `claude_code/cc-recovered-main/src/tasks/LocalAgentTask/LocalAgentTask.tsx`
- `claude_code/cc-recovered-main/src/services/AgentSummary/agentSummary.ts`

关注点：

- 子任务如何隔离上下文；
- 子 agent 输出如何摘要化回传主上下文；
- agent memory snapshot / resume 的边界；
- research / review / verification 这类 agent 的职责切分。

不要照搬：

- coding agent 的工具权限集合；
- 多 agent UI 和远程 agent 平台能力。`agent-os` 早期只需要“隔离执行 + 摘要回传 + artifact 引用”。

### Session Resume / Transcript Recovery

优先参考：

- `claude_code/cc-recovered-main/src/query.ts`
- `claude_code/cc-recovered-main/src/utils/messages.ts`
- `claude_code/cc-recovered-main/src/utils/analyzeContext.ts`
- `claude_code/cc-recovered-main/src/tools/AgentTool/resumeAgent.ts`

关注点：

- 会话历史进入模型前如何恢复、清理和预算；
- resume 时哪些内容是用户可见历史，哪些是模型工作上下文；
- compact / summary / memory snapshot 如何影响恢复后的上下文。

不要照搬：

- Claude Code 面向项目代码编辑的 transcript 假设；
- 在 Memory V2 已存在的情况下重复建设一套长期记忆。

## 当前优先级

Stage 1 优先阅读：

- Context Runtime / Diagnostics / Token Budget
- Message Normalization / History Cleaning
- Tool Result Folding / Artifact Reference 的预算部分

Stage 2 优先阅读：

- Compact / Prompt Too Long Recovery
- Slash Commands / Operator Control Plane
- Tool Result Folding / Artifact Reference 的外置引用部分

Stage 3 优先阅读：

- SubAgent / Isolated Worker
- Session Resume / Transcript Recovery

## Reference Check 模板

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
