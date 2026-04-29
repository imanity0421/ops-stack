# Claude Code Reference

> **本文定位**：`agent-os-runtime` 参考 `claude_code/cc-recovered-main` 研发时的**借鉴依据 + 差距矩阵 + 源码导航**单一 reference 文档。
>
> **与 [ARCHITECTURE.md](ARCHITECTURE.md) 的边界**：
>
> - ARCHITECTURE 是稳定架构总纲——决定 agent-os 自己的 4 视图、6 不变量、6+1 stage 路线、反模式抗体清单。Claude Code 在 ARCHITECTURE 中只作为**参考对象出现在第 5 节**（5 条核心借鉴机制 + 不照搬清单）。
> - 本文是借鉴依据的**完整版**——包含 14 项能力的差距矩阵、不借鉴 / 暂缓清单的具体能力 / 模块条目、7 个能力域的源码路径与关注点、Reference Check 模板。
> - 编辑分工：架构判断动 ARCHITECTURE；借鉴细节、源码定位、Reference Check 流程动本文。
>
> **核心原则**：**不纯硬造，也不无脑抄 Claude Code**。Claude Code 是参考对象，不是目标架构。每次借鉴都必须经过分析和取舍，把可迁移的 Harness 机制转译成更适合当前 Agno-based `agent-os` 的实现方案。

---

## 0. 文档定位（升级历史与边界）

### 升级历史

- **V0（2025）**：纯源码导航——只列 7 个能力域 + 源码路径 + 关注点。
- **V1（2026-04-29）**：从源码导航**扩展为单一 reference 文档**——合并原 [CLAUDE_CODE_REFERENCE_ROADMAP.md](archive/CLAUDE_CODE_REFERENCE_ROADMAP.md) 中的借鉴论证、差距矩阵、不借鉴清单。原 ROADMAP 的"5 大模块 / 阶段路线 / 优先级总表"已被 ARCHITECTURE 取代，故 ROADMAP 整体废弃，仅保留作历史归档。

### 与 ARCHITECTURE 的边界（再次强调）

| 维度 | ARCHITECTURE | 本文 |
| --- | --- | --- |
| 架构判断（4 视图 / 6 不变量 / stage 路线 / 反模式抗体） | **唯一权威** | 不参与 |
| 借鉴机制清单（高层 5 条） | 第 5 节 | 与本文互补 |
| 借鉴依据（不对称对标论证 / 8 条 harness 治理能力 / 3 条自有主线） | 不写 | **§1 完整版** |
| 14 项能力代码级差距矩阵 | 不写 | **§2 完整版** |
| 不借鉴 / 暂缓的具体能力 / 模块 | 不写（3.6 节只写抽象反模式） | **§3 完整版** |
| 7 个能力域源码路径 + 关注点 + Reference Check 模板 | 不写 | **§4 完整版** |

---

## 1. 借鉴依据（不对称对标）

### 1.1 Claude Code 与 agent-os 不能严格同构对标

Claude Code 与 `agent-os` 不能严格同构对标。

- **Claude Code** 更接近 **LLM-native coding harness**——模型承担大量任务策略判断，Harness 负责 query loop、message normalization、tool execution、context compact、transcript recovery、permission 和 UI 控制面。
- **agent-os** 更接近 **Agno-based business agent runtime**——Agno 提供 Agent Core 与基础工具调用循环；`agent-os` 在其外侧构建 Memory V2、ContextBuilder、skill manifest、业务记忆和商业交付逻辑。

正确的对标方式不是把 Claude Code 整体搬过来，而是拆出它**成熟的运行时治理能力**——把 Harness 工程能力转译进 Agno 外围的 Harness Runtime 层，**不照搬** coding 外壳、IDE 形态、代码工具链。

### 1.2 8 条值得借鉴的 Harness 运行时治理能力

| 能力 | 价值 | 落到 agent-os 的形态 |
| --- | --- | --- |
| Context Runtime Lifecycle | 上下文生命周期治理 | ContextBuilder 4 层结构 + 生命周期补齐（Stage 1） |
| compact / auto compact / reactive recovery | 长会话压缩 + 失败自救 | manual compact + auto compact suggestion mode（Stage 3） |
| tool result storage / microcompact | 长 tool result 外置引用 | Artifact Registry + `<artifact ref>`（Stage 2） |
| transcript / resume / recovery | 跨会话恢复 | `/task resume` 实时合成（Stage 4） |
| operator commands | 控制面入口 | `/context` / `/compact` / `/artifact` / `/skill`（贯穿） |
| observability / context diagnostics | 调试与归因 | ContextTrace + `/context` 诊断（Stage 1） |
| subagent sandbox | 隔离子代理 | SubAgent ContextSandbox（Stage 7+） |
| hook lifecycle | 扩展点 | PreCompact / PostCompact / SessionStart（Stage 7+） |

### 1.3 3 条 agent-os 必须自己发展的主线

借鉴 Claude Code 的同时，agent-os 应保留并深化自有护城河：

- **Memory V2 的长期业务记忆分层**——Mem0 / Hindsight / Asset Store 的 scope / authority / usage_rule / supersedes 语义，远超 Claude Code 的 memory file 形态。
- **可热插拔的业务 skill**——从 manifest 升级为业务配方（context pack + voice pack + output contract + rubric + brief extractor）。
- **商业交付能力**——品牌语气、brief 抽取、质量尺子、feedback-to-learning。这些是 Claude Code coding harness 不存在的能力。

---

## 2. 代码级差距矩阵

下表只列大模块与关键代码锚点，不把规划拆成实现 TODO。

| 能力 | Claude Code 参考位置 | agent-os 当前位置 | 差距判断 | 规划态度 |
| --- | --- | --- | --- | --- |
| 主 query loop | `src/query.ts`、`src/QueryEngine.ts` | Agno `Agent.run`、`src/agent_os/agent/factory.py` | Claude Code 自管消息循环；agent-os 依赖 Agno 高层抽象 | 不急着重写 Agno；先在 Agno 外围补 Harness 层 |
| message normalization | `src/utils/messages.ts` | `src/agent_os/context_builder.py`、Agno session message | Claude Code 有 API-bound normalization 与 tool invariant；agent-os 主要做 prompt 文本装配 | 借鉴思想，建立发送前统一清洗 / 投影层 |
| ContextBuilder | `src/context.ts`、`src/utils/api.ts`、`src/query.ts` | `src/agent_os/context_builder.py`、`src/agent_os/runtime_context.py` | agent-os 四层结构更清晰；Claude Code 生命周期治理更强 | 保留四层，补生命周期 |
| context diagnostics | `src/commands/context/*`、`src/utils/analyzeContext.ts`、`src/utils/contextAnalysis.ts` | `ContextTrace`、`context_trace_log` | agent-os 有 trace，但缺面向用户 / 开发者的 `/context` 诊断 | Stage 1 优先借鉴 |
| compact service | `src/services/compact/*`、`src/commands/compact/*` | TaskSummary / history cap / char budget | agent-os 缺完整 conversation compact、compact boundary、post-compact rehydration | Stage 3 核心借鉴 |
| auto compact / blocking limit | `src/services/compact/autoCompact.ts`、`src/query.ts` | `ContextCharBudget`、`context_hard_budget` | Claude Code 有 token 窗口阈值与失败熔断；agent-os 主要字符预算 | Stage 1 做探针，Stage 3 做 suggestion mode，Stage 7+ 做 blocking |
| tool result budget | `src/utils/toolResultStorage.ts`、`src/services/compact/microCompact.ts` | `clean_history_messages`、tool output fold | agent-os 已能折叠历史工具输出，但无 artifact / digest / replay 生命周期 | Stage 2 重点补齐 |
| transcript / resume | `src/utils/sessionStorage.ts`、`src/utils/conversationRecovery.ts` | Agno session DB、CLI / Web session read | Claude Code 对 compact boundary、unresolved tool_use、content replacement 更成熟 | Stage 4 补 Harness 化恢复 |
| memory system | `CLAUDE.md`、memory files、session memory、auto extract | `src/agent_os/memory/*`、`MEMORY_SYSTEM_V2.md` | agent-os 长期业务记忆架构更清晰；Claude Code 产品化接入更成熟 | 保留 Memory V2，借鉴自动接入与解释能力 |
| skill system | `src/skills/*`、plugins、tool discovery | `manifest_loader.py`、`agent/skills/*`、`factory.py` | Claude Code 偏 coding workflow；agent-os 应偏业务配方 | Stage 5 强化 output contract / context pack |
| commands | `src/commands/*` | CLI 子命令、Web demo | Claude Code 控制面更成熟 | Stage 1-5 持续增加 `/context` / `/compact` / `/artifact` / `/skill` |
| hooks | `utils/hooks.ts`、compact / session hooks | 暂无完整 lifecycle hook | Claude Code 有 pre / post compact、session start 等扩展点 | Stage 7+ 引入轻量 hook |
| subagent | `AgentTool`、coordinator、task modules | 未实现 | Claude Code 更成熟 | Stage 7+，依赖 artifact / compact 稳定后再做 |
| permissions / policy | tool permission hooks、permission modes | Memory Policy、scope、manifest 工具白名单 | agent-os 数据 scope 清晰，工具权限弱 | 平台化前轻量补，不抢效果主线 |

---

## 3. 不借鉴 / 暂缓清单

> **与 ARCHITECTURE 3.6 的互补**：ARCHITECTURE 3.6 反模式偏抽象（"不做企业平台"、"不做 TTL 自动清理"、"不做服务端 context management"）；本文偏具体能力 / 模块（"不照搬 LSP / git diff"、"在 artifact / compact 稳定前不做 SubAgent"）。两者搭配使用。

### 3.1 不借鉴的 Claude Code 能力（属 coding 产品形态或平台外壳）

- **LSP、代码编辑、git diff、PR review、shell 安全分类等 coding 专属能力**——agent-os 服务文科类任务（商业 / 运营 / 写作 / 策划），coding 工具链与业务主线无关。
- **Vim、terminal UI、voice、desktop handoff 等交互外壳**——agent-os 的 CLI / Web 是 thin presentation layer，不发展 IDE 形态。
- **大规模 IDE bridge、远程会话、分享体系**——除非未来产品形态明确需要。
- **完整插件市场、组织级策略、计费、复杂权限 UI**——属企业平台能力，与个人级 SOTA 定位冲突。
- **Claude Code 的 memory file 形态**——不应替代 Memory V2 的分层语义（scope / authority / supersedes）。

### 3.2 暂缓借鉴（条件触发）

- **过早 model-driven `manage_context` / Rewind / Clear**——模型自驱不可逆操作风险高，等 Stage 7+ 真痛点出现再做。
- **在没有 artifact / compact / trace 的情况下做 SubAgent**——SubAgent 必须站在隔离 + 可观测 + artifact 引用的稳定底座上，强制依赖 Stage 2-4 完成。
- **全自动记忆抽取并直接写入长期记忆**——会污染长期记忆库，agent-os 始终走"candidate review → 用户确认入库"路径。
- **重型 Deliverable Version Graph**——Stage 2 仅做 current / previous / final 轻量版本，复杂版本树推到 Stage 7+ 真实痛点出现后。
- **大型 project workspace**——单 task 已能服务个人级场景，project workspace 是企业平台需求。
- **完整 Skill Router / Composition**——见 ARCHITECTURE 3.6（默认不做，除非 Stage 7+ 出现硬证据）。
- **大型 A/B 实验平台**——基础 trace + GC 已足够个人级迭代验证；A/B 平台是企业平台需求。
- **多租户、计费、复杂权限 UI、分布式锁**——见 ARCHITECTURE 3.6（永远不做）。

### 3.3 反模式（具体行为级）

> ARCHITECTURE 3.6 反模式偏抽象原则；以下是具体编码行为级反模式，写代码时要避免：

- 把动态记忆重新塞回静态 `instructions`——破坏 ContextBuilder 4 层结构。
- 为了看起来智能，让模型决定不可逆上下文删除——见 ARCHITECTURE 不变量"经验沉淀显式可解释"。
- 把工具长结果直接写入可回灌历史——必须走 artifact 引用路径。
- 把单次用户反馈直接永久化为全局规则——必须走 candidate review 路径。
- 为了平台完整性牺牲当前交付效果——见 ARCHITECTURE 第 0.4 节哲学锚点。
- 用 Claude Code 的 coding 专属能力污染商业业务主线——见本文 §3.1。

---

## 4. 源码导航与 Reference Check

### 4.1 使用方式

每个 Stage / battle 开工前，如果任务属于"Claude Code 可参考优先"的范围，先做一次 Reference Check：

1. 在本节找到对应能力域。
2. 阅读列出的 Claude Code 源码文件，必要时再沿 import / call site 扩展。
3. 在实现前写明：
   - 已阅读的 Claude Code 文件；
   - 可借鉴的机制；
   - 因 `agent-os` 基于 Agno 架构而不能照搬的部分；
   - 选择性借鉴后的适配方案；
   - 本次落到 `agent-os-runtime` 的实现边界。
4. 如果本节没有对应条目，先用代码搜索定位，再把稳定结论补回本文。

### 4.2 7 个核心参考域

#### Context Runtime / Diagnostics / Token Budget

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

#### Compact / Prompt Too Long Recovery

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

#### Message Normalization / History Cleaning

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

#### Tool Result Folding / Artifact Reference

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

#### Slash Commands / Operator Control Plane

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

#### SubAgent / Isolated Worker

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
- 多 agent UI 和远程 agent 平台能力。`agent-os` 早期只需要"隔离执行 + 摘要回传 + artifact 引用"。

#### Session Resume / Transcript Recovery

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

### 4.3 阅读优先级（按 Stage）

Stage 1 优先阅读：

- Context Runtime / Diagnostics / Token Budget
- Message Normalization / History Cleaning
- Tool Result Folding / Artifact Reference 的预算部分

Stage 2 优先阅读：

- Tool Result Folding / Artifact Reference 的外置引用部分
- Slash Commands / Operator Control Plane（`/artifact` / `/task`）

Stage 3 优先阅读：

- Compact / Prompt Too Long Recovery
- Slash Commands / Operator Control Plane（`/compact` 完整版）

Stage 4 优先阅读：

- Session Resume / Transcript Recovery

Stage 7+ 优先阅读：

- SubAgent / Isolated Worker

### 4.4 Reference Check 模板

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

---

**修订记录**：

- 2025（V0）：初版——纯源码导航，7 个能力域 + 源码路径 + 关注点 + Reference Check 模板。
- 2026-04-29（V1）：从源码导航**扩展为单一 reference 文档**——
  - 新增 §0 文档定位 + 与 ARCHITECTURE 边界划分。
  - 新增 §1 借鉴依据（合并自原 ROADMAP §2，含不对称对标论证、8 条 harness 治理能力、3 条 agent-os 自有主线）。
  - 新增 §2 代码级差距矩阵（合并自原 ROADMAP §3，14 项能力完整保留）。
  - 新增 §3 不借鉴 / 暂缓清单（合并自原 ROADMAP §4 + §9，与 ARCHITECTURE 3.6 反模式互补）。
  - 原"使用方式"+"7 个核心参考域"+"当前优先级"+"Reference Check 模板"重组为 §4。
  - 原 [CLAUDE_CODE_REFERENCE_ROADMAP.md](archive/CLAUDE_CODE_REFERENCE_ROADMAP.md) 已废弃（顶部加 deprecated 标记），仅保留作历史归档。
