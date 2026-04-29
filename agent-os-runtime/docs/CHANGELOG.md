# 变更日志

格式基于实际交付，版本号与 `pyproject.toml` / `agent_os.__version__` 对齐。

## [Unreleased]

本节按 `### Stage X` / `### 文档` / `### 改进` / `### 新增` / `### 修复` / `### 破坏性变更` 分组；Stage 2 起用 `### Stage 2` 记录 battle 完成项。

### Stage 5

- **Battle 4：SR 平等性 + 跨 skill artifact 共享 invariant 工程验证**（2026-04-30，done-local）
  - `branch_task` 的 resume final-state 合成路径支持透传 mock skill schema registry，确保 branch / resume 端到端诊断能看到 active skill fragment。
  - 用 `MockSkillA` / `MockSkillB` 异类字段集验证注册 → resume/branch 装配 → fake ER spin up 的平等路径，两个 skill 均不触发 fragment skipped。
  - 验证跨 skill 共享只走 artifact ref：MockSkillA 产出 artifact，MockSkillB 的独立 task 通过 `current_artifact_refs` 恢复交付物内容，不共享 A/B schema 字段。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py tests/core/test_context_diagnostics.py`；`python -m ruff check src tests`。

- **Battle 3：缺失 skill fragment fallback 诊断**（2026-04-30，commit `7132f3f`）
  - 新增 `SkillFragmentResolution`，将无 active skill、无 provider、provider 返回 None 三类 core-only fallback 显式区分为 `no_active_skill_id` / `provider_missing` / `fragment_missing`。
  - `resume_task` / `branch_task` 的 diagnostics 增加 `active_skill_id`、`skill_fragment_skipped`、`skill_fragment_skip_reason`；缺 fragment 不报错，仍走 core-only schema。
  - `/context` 归一化与 Markdown 输出展示 skill fragment fallback，`task resume --json` payload 可直接作为 `context-diagnose --resume-diagnostics-json` 输入。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py tests/core/test_context_diagnostics.py`；`python -m ruff check src tests`。

- **Battle 2：ER `start_resumed_session` 真实 Agno spin up**（2026-04-30，commit `6a8af17`）
  - 新增 ER resumed-session 入口，`start_resumed_session(prompt, session_meta)` 负责创建 Agent 并调用 Agno `agent.run()`，CLI 不直接持有运行细节。
  - `resume_task` / `branch_task` 从 prompt-only 升级为可委托 ER 的 CTE 路径，返回 `runtime_status` / `runtime_session_id` / `runtime_session` 诊断；runtime 失败时返回 error，不把 prompt-only 当成功兜底。
  - `/task resume` 与 `/task branch` 默认通过 CTE 触发 ER spin up；测试通过 fake runtime 覆盖 resume fork、branch session、CLI JSON 和错误路径。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py`；`python -m ruff check src tests`。

- **Battle 1：SR 框架 schema fragment 真实合成**（2026-04-30，commit `d196fdc`）
  - `CompactSummary` v2 增加 `compose_compact_summary_schema()` 动态合成路径，固定 CTE-owned `core`，并按 active skill 注入 SR-owned `skill_state` fragment。
  - 新增轻量 `SkillSchemaProviderRegistry`，支持 `skill_id -> SkillSchemaProvider` 注册与 fragment 查询；`CompactSummaryService` 可通过 provider 或 registry + active skill id 获取 fragment。
  - 用 `MockSkillA` / `MockSkillB` 异类字段集验证 SR 平等注册与合成；fallback compact 仍保持 `skill_state=None`，不引入真实业务字段或 voice 字段。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_compact_v1_to_v2_migration.py`；`python -m ruff check src tests`。

### Stage 4

- **Battle 5：Golden Case GC-Resume 收口**（2026-04-30，commit `4a0cf90`）
  - [GC_SPEC.md](GC_SPEC.md) 新增 Stage 4 GC6-8 字段级断言，覆盖隔天 resume fork 恢复、分支对照隔离、短 session connect + `/context` 可见性。
  - Baseline Trace 追加 Trace 3-5：stale resume fork、branch compact refs 隔离、short session connect + `context-diagnose --resume-diagnostics-json`。
  - 测试补齐 GC6 / GC8 防退化断言，并复用 Battle 2 的 branch isolation 测试覆盖 GC7。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py tests/core/test_context_builder.py tests/core/test_context_diagnostics.py tests/core/test_artifact_store.py`；`python -m ruff check src tests`。

- **Battle 4：Resume Trace + `/context` 集成**（2026-04-30，commit `a98424d`）
  - `task resume` 的 `resume_diagnostics` 扩展为稳定结构，覆盖 connect/fork 决策路径、force flag、session age/context usage、tail message count、artifact/pinned ref count 与 `voice_pack_skipped`。
  - final_state 观测字段进入诊断：`deliverable_inline_level`、`current_deliverable_chars`、`deliverable_fallback_chain`，用于观察 `full` / `tail` / `none` 命中。
  - `/context` 诊断新增 `resume_diagnostics` JSON 字段与 Markdown `### Resume Diagnostics` 段；`context-diagnose --resume-diagnostics-json` 可读取 `task resume --json` payload。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py tests/core/test_context_builder.py`；`python -m ruff check src tests`。

- **Battle 3：Artifact CoW v0**（2026-04-30，commit `fc1d240`）
  - `ArtifactStore` 启用 `originating_session_id` 并自动迁移旧库；新 artifact 默认以创建 session 作为 origin。
  - 新增 `update_artifact_content` 写入口：origin session 内原地更新并重置 digest，跨 session 修改时强制复制为新 artifact，保留旧版本给原分支引用。
  - CoW 时可在同一 SQLite 事务内更新当前 session 的 `CompactSummary.core.current_artifact_refs`，并返回 `cow_from` / `compact_refs_updated` 诊断；`artifact update` CLI 暴露该路径。
  - 验证：`python -m pytest tests/core/test_artifact_store.py tests/core/test_cli.py tests/core/test_task_memory.py`；`python -m ruff check src tests`。

- **Battle 2：`/task branch` v0**（2026-04-30，commit `6c2b94c`）
  - `sessions` 表扩展 `parent_session_id` / `branch_role`，旧库自动迁移；`task_id` 继续复用 `active_task_id`，主线判断继续由 `tasks.current_main_session_id` 反查。
  - 新增 CTE `branch_task` 平铺入口与 `task branch` CLI；分支 session 通过 source session final_state 实时合成首轮 prompt，不复制 compact summary，也不改当前主线 session。
  - resume fork 新主线时写入 `branch_role=main` 与 `parent_session_id`，分支 session 写入 `branch_role=branch`，main/branch 的 CompactSummary 按 `(session_id, task_id)` 独立演化。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py`；`python -m ruff check src tests`。

- **Battle 1：`/task resume` v0**（2026-04-30，commit `c53ad7f`）
  - 新增 CTE `resume_task` 平铺入口，实时合成 resume final_state（CompactSummary + uncompacted tail + artifact refs / pinned refs）并生成纯文本 resume prompt。
  - `task resume` CLI 支持 B5.c connect/fork 自动判断与 `--force-fork` / `--force-connect` override；fork 时更新 `tasks.current_main_session_id`，connect 时复用当前 session。
  - `voice_pack=None` 时跳过 inline 段并在 prompt/diagnostics 保留 `voice_pack_skipped` 语义；Stage 4 只消费 `pinned_refs`，不实现 pin/unpin。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_cli.py tests/core/test_context_builder.py`；`python -m ruff check src tests`。

### Stage 3

- **CompactSummary v1 + Manual Compact**（2026-04-29，commit `98f7953`）
  - 新增结构化 `CompactSummary` / `CompactSummaryCore` 与 `SkillSchemaProvider` Protocol，按 system-state / LLM-generated state 分离维护 compact 字段。
  - 新增 `compact_summaries` SQLite 存储与 `compact run/show` CLI；无 LLM key 时使用 deterministic fallback，保证本地可验证。
  - `ContextBuilder` 支持注入 `<compact_summary>` rehydration block，`/context` JSON/Markdown 新增 `compact_diagnostics` 与 budget suggestion signal。
  - [GC_SPEC.md](GC_SPEC.md) 追加 GC4 / GC5 Stage3 字段级断言；[OPEN_DECISIONS.md](OPEN_DECISIONS.md) A7 增加 Stage 3 收口备注（schema fragment 签名落地）。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_context_builder.py tests/core/test_cli.py tests/core/test_context_diagnostics.py`；`python -m ruff check src tests`。

### Stage 2

- **Battle 1+2：Task Entity v0 + Artifact Registry v0**（2026-04-29，commit `cca7273`）
  - Task Entity v0：`tasks` 5 字段实体落地，支持 create / list / archive / unarchive，并提供 thin `task` CLI。
  - Artifact Registry v0：新增 SQLite 原文层，支持 artifact 与 task / session 绑定、digest fallback、软归档。
  - ContextBuilder 支持 `<artifact ref>` prompt 装配，模型只看到 ref / digest，不回灌 artifact 全文。
  - 验证：`python -m pytest tests/core/test_task_memory.py tests/core/test_context_builder.py tests/core/test_cli.py tests/core/test_artifact_store.py`；`python -m ruff check src tests`。
- **Battle 3：Tool Result Artifactization**（2026-04-29，commit `e5000cd`）
  - 新增 `ToolResultArtifactizer`，长 tool result 写入 `ArtifactStore`，history 中仅保留稳定 `<artifact ref>` + digest。
  - `ArtifactStore` 支持 `stable_key` 去重查询，重复清洗同一 tool result 时复用同一 artifact。
  - `ContextBuilder.clean_history_messages_with_report()` 支持显式注入 artifactizer，默认不改变运行时行为；trace 增加 `tool_artifactized` 计数。
  - 验证：`python -m pytest tests/core/test_tool_result_artifactization.py tests/core/test_context_builder.py tests/core/test_artifact_store.py`；`python -m ruff check src tests`。
- **Battle 4：Long Source Artifactization**（2026-04-29，commit `2e24307`）
  - 新增 `SourceArtifactizer`，长用户素材与长 assistant deliverable 可写入 `ArtifactStore`，prompt/history 只保留稳定 `<artifact ref>` + digest。
  - `ContextBuilder.clean_history_messages_with_report()` 支持显式注入 source artifactizer，默认不改变 CLI/Web 主运行时行为；trace 增加 `source_artifactized` 计数。
  - 当前用户长 source 可在显式注入时 artifact 化为 `<artifact ref>`，避免大段 source 直接进入 prompt。
  - 验证：`python -m pytest tests/core/test_source_artifactization.py tests/core/test_context_builder.py tests/core/test_artifact_store.py`；`python -m ruff check src tests`。
- **Battle 5：Artifact Lifecycle Commands**（2026-04-29，commit `3241da8`）
  - 新增 `artifact list/show/archive` CLI，支持 artifact 原文层精确查看、JSON 输出、raw 输出与软归档。
  - 新增 `blob gc --orphan` dry-run 命令，只列出无有效 task 的 orphan artifact，不做删除或 TTL 清理。
  - `Settings` 增加 `AGENT_OS_ARTIFACT_STORE_PATH`，ArtifactStore 增加 all/orphan 列表能力。
  - 验证：`python -m pytest tests/core/test_cli.py tests/core/test_artifact_store.py tests/core/test_source_artifactization.py tests/core/test_tool_result_artifactization.py`；`python -m ruff check src tests`。
- **Battle 6：Trace + `/artifact` + `/context` Integration**（2026-04-29，commit `c305d16`）
  - 新增 `ArtifactDiagnostics`，从已组装 prompt / trace 统计 artifact ref 数、pending digest 数、artifact 字符占比、tool/source artifactized 计数与当前用户 source artifact 化状态。
  - `/context` JSON 输出结构化 `artifact_diagnostics`，Markdown 输出新增 `Artifact Diagnostics` 小节；`context-diagnose` 支持显式 `--artifact-refs-json` 调试入口。
  - 新增最小 `docs/GC_SPEC.md`，记录 Stage 2 artifact trace/context 字段级断言与 baseline 验证口径。
  - Reference Check：借鉴 Claude Code `/context` 的真实上下文视图统计与 replacement record 的可复原决策思想，不引入 query loop 状态机。
  - 验证：`python -m pytest tests/core/test_context_builder.py tests/core/test_cli.py tests/core/test_tool_result_artifactization.py tests/core/test_source_artifactization.py tests/core/test_artifact_store.py`；`python -m ruff check src tests`。

### 文档

- **文档权威层级整理**：明确 [ARCHITECTURE.md](ARCHITECTURE.md) 为唯一架构权威，[OPEN_DECISIONS.md](OPEN_DECISIONS.md) 承载开放决策与 Stage 2 battle 顺序，[CLAUDE_CODE_REFERENCE_INDEX.md](CLAUDE_CODE_REFERENCE_INDEX.md) 承载 Claude Code 借鉴依据 / 差距矩阵 / 源码导航；旧 roadmap / sprint / stage 过程文档归档至 [archive/](archive/)。
- **文档入口合并**：原 `docs/README.md` 已撤并至仓库 [README.md](../README.md) §**文档与阅读顺序**（避免根目录与 `docs/` 双 README）；[AGENTS.md](../AGENTS.md) 与相关交叉链接已更新。
- 统一 **Hindsight `supersedes_event_id`** 表述：**append-only 存储，召回层降权**（与 `HindsightRetrievalPolicy` 一致），修正 [ENGINEERING.md](ENGINEERING.md)、[OPERATIONS.md](OPERATIONS.md)、[MEMORY_SYSTEM_V2.md](MEMORY_SYSTEM_V2.md)、[examples/ingest_post_samples.md](examples/ingest_post_samples.md) 中旧版「从召回剔除/隐藏」等措辞。
- **Phase 8 Stage 4 启动前决策收口**（2026-04-29）：[OPEN_DECISIONS.md](OPEN_DECISIONS.md) 新增 F 章节，固化 Stage 4 5 个 battle 顺序（`/task resume` v0 / `/task branch` v0 / Artifact CoW v0 / Resume Trace + `/context` 集成 / GC-Resume 收口）+ Status 维护规则（与 D1 同构）+ Stage 4 启动前已敲定决策摘要（A4-ii / A5 / B5.c / H5 task_history 路径 / S3 voice_pack=None fallback）+ 执行节奏 + F4 暂缓项；A3 / A4 / A5 / A6 / B5 五条均追加 Stage 4 启动前确认 / 路径决策段，其中 A4 直接采用 **Phase 8 落地修正版**——经实测核查 [task_memory.py:138-146 / 425](../src/agent_os/agent/task_memory.py)，仅扩展现有 `sessions` 表 +2 列（`parent_session_id` + `branch_role`），`task_id` / `last_active_at` / `is_main` 全部复用现有字段不新增。[ARCHITECTURE.md](ARCHITECTURE.md) §4 Stage 4 一句话承诺末尾追加 F 引用（与 §609 Stage 2 末尾 D 引用同构，属视图缺失补足型自完备性补丁）；ARCH **主干 0 修订**。本次 Phase 8 不开始任何 Stage 4 代码实现，只锁定文档层决策。
- **Phase 9 Stage 5 启动前 SR 抽象重构 / Level 2 架构演进修订**（2026-04-30）：本 Phase 是 [ARCHITECTURE.md](ARCHITECTURE.md) 升格为单一架构权威以来**首个 Level 2 架构演进修订**，引入并执行了 §6 升级后的 3 级修订门槛。
  - **触发证据**：用户在 Stage 4 收口、Stage 5 启动前 Phase 启动时提出 4 项深度架构疑虑——① Stage 5 重点 `SR.business_writing` 是否会将 agent-os 固化成"专门兼容写文案 skill 的 OS"，未来扩展数据分析 / 商业策划 / 编程辅助等异类 skill 时被卡死？② placeholder 实现是否会损害 SOTA 度。③ SR 字段制定缺失明文规则（brand_voice / audience 标签分类、商业策划 vs 商业文案是否同一 SR、SR 字段越多越好还是越少越好）。④ 当前 Stage 方案合理性受质疑——业务字段固化与基础设施抽象耦合过深。**推翻** [ARCHITECTURE.md](ARCHITECTURE.md) §3.2 / §3.6 之前拒绝 `shared_skill_context` 改名时的论据"污染场景不存在"——污染场景已实际出现。
  - **拒绝清单**：方案 A（5a/5b 拆分，命名混淆）/ 方案 C（Stage 5/6 合并，范围拥堵）/ 方案 Z（保留 `business_writing_pack` 命名 + 加边界注释，命名偏向）/ 方案 X（保留 Layer 2 + 通用化重命名为 `shared_skill_context`，跨 skill 共享需 schema 层会污染 core）/ 路径 2（共享字段升 core，违反 core 通用层硬约束）。详见 [OPEN_DECISIONS.md](OPEN_DECISIONS.md) Phase 9 修订记录。
  - **影响域**：[ARCHITECTURE.md](ARCHITECTURE.md) 主干修订 9 处（§6 升 3 级门槛 / §3.2 删 Layer 2 + 改两层 schema + 加 SR 平等承载 / §3.6 加 2 条新反模式抗体"未做先猜业务字段" + "跨 skill schema 字段共享" / §4 stage 路线 5/6/7/8+ 重拆 + mermaid 重绘 / §1.1 ER 模块加 `start_resumed_session` 入口签名 / §397 invariant 拆分 Memory candidate vs Voice Runtime / §0.4 哲学锚点新增 SR 平等承载 SOTA 条款 / 全文 stage 编号批量替换 / §3.5 GC 累积强化表 Stage 5 → Stage 7）+ [OPEN_DECISIONS.md](OPEN_DECISIONS.md) 新增 5 条决策（A9 SR 字段判别准则框架 / A10 建模派系选择推到 Stage 7 / B6 SR 切分规则 / B7 第一个 skill 选择推到 Stage 7 / B1 重打开备注）+ 新增 G 章节（Stage 5 5 battle 排序 + 启动前已敲定决策摘要 + 暂缓项）+ [compact.py](../src/agent_os/agent/compact.py) Schema v1 → v2（删 `business_writing_pack` + bump version + prompt 同步 + v1 → v2 反序列化兼容 fallback）+ 新增 [scripts/migrate_compact_v1_to_v2.py](../scripts/migrate_compact_v1_to_v2.py) 一次性数据迁移脚本 + 新增 [tests/core/test_compact_v1_to_v2_migration.py](../tests/core/test_compact_v1_to_v2_migration.py) + 测试中 schema_version 断言更新 + [GC_SPEC.md](GC_SPEC.md) GC4 措辞更新。
  - **不可回溯性**：本修订引入的"SR 框架对所有 skill family 平等承载"+ Stage 路线 5/6/7/8+ 编号 + "未做先猜业务字段" 反模式抗体在 Stage 7 第一次真实 skill 接入并验证后，**不允许通过 Level 1 自完备性补丁回退**——若必须修订，需重新启动 Level 2 Phase 并提交新一轮触发证据 + 拒绝清单 + 影响域评估 + 不可回溯性声明 4 字段。
  - **执行模式**：慢做 4 commit（Step 1 OPEN_DECISIONS / Step 2 ARCH 主干 / Step 3 代码迁移 / Step 4 CHANGELOG + grep 校验），每步用户审阅后推进；ruff 全清，targeted pytest 63 通过，全量 pytest 444 通过。
  - **下一动作**：Stage 5 (SR Framework v0) Battle 1 代码 PR；启动前所需决策已全部固化在 [OPEN_DECISIONS.md](OPEN_DECISIONS.md) G 章节，**无需再做 Stage 5 启动前 Phase**（与 Phase 8 / F 章节同模式区别于此点）。

### 改进

- **Memory V2 编排**：`retrieve_ordered_context` 四层 Markdown 组装收敛至 `MemoryController.retrieve_ordered_context` + `agent_os.memory.ordered_context`；Mem0/Hindsight/Asset 块格式化提取到 `agent_os.memory.context_formatters`。
- **Memory V2 验收测试**：补齐四层完整召回、Agent 工具入口、Graphiti legacy 开关、Hindsight 租户隔离、Asset scope 可见性与 Mem0 V2 metadata 落盘的最小验收覆盖。
- **Mem0 检索治理**：`search_profile` 跨桶合并时对**相同正文**保留 `recorded_at`/`created_at` 更晚的命中；`ENGINEERING.md` 同步 Graphiti 系统分区 + legacy 只读、`AGENT_OS_GRAPHITI_*` 索引。
- **Hindsight 频次 / 合并 / supersedes**：JSONL 支持 `supersedes_event_id`、`weight_count`；`search_lessons` 对被 supersedes 的事件在排序中**降权**（append-only，不删行）、按规范化正文合并桶并展示「同类×n，总权重×w」、对数频次加分；`AGENT_OS_HISTORICAL_ENABLE_FREQ_MERGE` 可关合并（仍保留 supersedes **降权**计分）；`UserFact` 增加对应字段。
- **摄入 / 工具透传**：`run_ingest_v1` 与 Web `POST /ingest`（`IngestV1In`）在 `target=hindsight` 时支持 `supersedes_event_id`、`weight_count`；工具 **`record_task_feedback`** 同步可选参数。
- **Graphiti 权限持久化**：新增 `data/graphiti_entitlements.json` 语义与 `GraphitiEntitlements` 解析器；`search_domain_knowledge` 改为“文件优先、env 兜底”；CLI 新增 `graphiti-entitlements`（show/set/remove）。
- **Graphiti 运维补强**：新增 `docs/examples/graphiti_entitlements.example.json`；`doctor` 增加权限文件结构与字段类型校验；Web 增加可选内网管理接口 `/api/admin/graphiti-entitlements*`（默认关闭，且限制本机访问）。
- **Graphiti 管理安全与审计**：Web 管理接口增加 token 鉴权（`AGENT_OS_WEB_ADMIN_API_TOKEN(S)` + `x-admin-token`/Bearer），CLI 与 Web 的 entitlements 变更统一写入审计 JSONL（`AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH`）。
- **Graphiti 权限热加载**：新增 `GraphitiEntitlementsProvider`，支持缓存 TTL（`AGENT_OS_GRAPHITI_ENTITLEMENTS_CACHE_TTL_SEC`）并在权限文件 mtime / env 变化时自动失效重载；`GraphitiReadService` 可手动 `invalidate_entitlements_cache()`。
- **Graphiti 并发写保护**：权限文件写入改为锁文件互斥 + 原子替换（`os.replace`），审计日志 append 加锁；新增 `AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC` 与并发测试覆盖。
- **Graphiti 乐观并发控制**：权限文档新增 `revision`；CLI `graphiti-entitlements` 与 Web 管理写接口支持 `expected_revision` 冲突检测（CLI 冲突返回码 `2`，Web 返回 `409`），防止“最后写入覆盖”。
- **冲突提示改进**：权限写冲突时返回 `expected/actual revision` 与重试提示（CLI stderr 与 Web 409 detail 结构化字段）。
- **审计日志运维策略**：Graphiti 权限审计支持滚动切分与保留期（`AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_BYTES` / `..._MAX_FILES` / `..._RETENTION_DAYS`）。
- **Web 管理接口测试**：新增 `tests/core/test_web_admin_api.py` 覆盖鉴权成功/失败、revision 冲突、审计落库链路。
- **Web 管理接口幂等键**：支持 `Idempotency-Key`（同键同请求体返回缓存结果、不同请求体复用同键返回 `409`），减少网络重试导致的重复写与重复审计。
- **Graphiti 权限后端收缩**：回退 `sqlite` / `postgres` entitlements 后端，当前阶段仅保留文件权限模型，避免 Memory V2 被权限平台化工作牵引。
- **工程卫生**：模块 docstring 置于文件首（符合 PEP 236 + Ruff E402）；`observability` 在顶层 token 为 0 时聚合 ``RunMetrics.details``；Web ``/chat`` 增加 ``reply_content_kind`` / ``structured`` 以支持 ``planning_draft`` 等结构化输出；**可选依赖** ``dev`` 含 ``lancedb`` 便于全量测试。
- **工程卫生（继续）**：``pyproject.toml`` 增加 ``[tool.ruff]``；CI 增加 ``ruff format --check``；全仓已 ``ruff format`` 对齐；Asset 入库单测改为显式 ``import lancedb``（缺依赖时失败而非 skip，与 ``.[dev]`` 一致）；``docs/OPERATIONS.md`` 默认安装改为 ``.[dev]`` 以匹配 CI。
- **Pre-commit**：仓库根 ``.pre-commit-config.yaml``（``ruff-check`` + ``ruff-format``）；``.[dev]`` 含 ``pre-commit``；见 ``docs/OPERATIONS.md`` 启用 ``pre-commit install``。

### 新增

- **P1.5 认知稳定性增强路线**：记录 Ephemeral Metadata、Memory Policy、Temporal Grounding、Session Summarizer 的设计路线；Forge 程序性记忆仅作为 `pending_review` 候选 SOP 待办，禁止自动写入干净知识库。
- **P1.5 认知稳定性增强实现（首批）**：`runtime_context` 注入临时时间/入口/skill；`Memory Policy` 服务端 gate 拒绝脏记忆写入；Mem0/Hindsight/Asset 检索渲染增加时间线提示；新增 `AGENT_OS_ENABLE_EPHEMERAL_METADATA` / `AGENT_OS_ENABLE_MEMORY_POLICY` / `AGENT_OS_ENABLE_TEMPORAL_GROUNDING` 等配置。
- **Task-aware Working Memory 设计**：将 Session Summarizer 收敛为同一 session 内的 `task_id` 自动管理 + task summary；采用保守边界检测、candidate/confirmed、有限回溯与 audit，跨 session 信息仍由 Mem0/Hindsight 承担。
- **Task-aware Working Memory 首批实现**：新增 `agent.task_memory`（SQLite task/message/summary/audit schema、`task_id` 自动生成、summary/index prompt helper）；`get_agent` 支持注入当前 task summary 与短 task index；新增 `AGENT_OS_ENABLE_TASK_MEMORY` 等配置。
- **Sprint 4 P3-7 Skill 评测协议**：核心仓保留 `tests/core/`；外部 skill pack 可自带 fixtures 与 pytest markers；仍统一 `run_e2e_eval_*` 引擎。
- **Sprint 4 P3-8 数据运维**：`agent_os.backup_data_core`、`scripts/backup_data.py` 生成本地 `data/` 候选文件 zip；`docs/DATA_BACKUP.md`（含 Mem0 官方/人工 SOP）；`backups/` 已加入 `.gitignore`。
- **Sprint 3 P2-5 可观测性**：`agent_os.observability`（`AGENT_OS_OBS` 日志行）；Web 示例中间件透传 **`X-Request-ID`**；**`/chat`** 结束打 `session_id` / `model` / `tools` / `elapsed_ms` / token 粗算。
- **Sprint 3 P2-6 数据摄入网关**：`agent_os.ingest_gateway.run_ingest_v1` + **`POST /ingest`**（`target=mem0_profile|hindsight|asset_store`）；样例见 `docs/examples/ingest_post_samples.md`；可选 **`AGENT_OS_INGEST_ALLOW_LLM`**。
- **Sprint 2 P1-3 系统宪法**：`agent_os.agent.constitutional` 固定「冲突解决序」并置于 `get_agent` 指令最前；`AGENT_OS_ENABLE_CONSTITUTIONAL`；`AgentManifestV1.constitutional_prompt` 可选补充。`retrieve_ordered_context` 说明与宪法互补。验收表见 `docs/examples/constitutional_test_cases.md`。
- **Sprint 2 P1-4 交付物契约**：`manifest.output_mode`=`structured_v1` + `output_schema_version`=`1.0` → Agno `output_schema`=`PlanStructuredV1`（`body_markdown` 承载长文）；内置 skill **`planning_draft`**。
- **Sprint 1 P0-2 会话持久化**：`config.Settings` 增加 `enable_session_db`、`session_sqlite_path`、`session_db_url`、`session_history_max_messages`；`agent_os.agent.session_db.create_session_db` 供 `get_agent` 挂接 Agno `db`；默认注入最近 N 条历史。Web 增加 `GET /api/session/messages` 供重启后拉取与模型一致的历史。

### 文档

- **Memory V2 运维**：`docs/OPERATIONS.md` 增补「Memory V2 运维」（环境变量、Graphiti legacy、`migrate_memory_v2`）；`MEMORY_SYSTEM_V2.md` 增补运维与迁移交叉说明。
- **历史 Sprint 实施路线图**：原 [docs/SPRINT_IMPLEMENTATION_ROADMAP.md](archive/SPRINT_IMPLEMENTATION_ROADMAP.md)（Sprint 1–4、DoD、Mermaid 设计图与实现落点）已归档；当前 stage 路线以 [ARCHITECTURE.md](ARCHITECTURE.md) §4 与 [OPEN_DECISIONS.md](OPEN_DECISIONS.md) D 为准。

### 新增

- **Sprint 1 P0-1 Skill 扩展协议**：`agent_os.agent.skills.loader` 白名单动态加载 `agent_os.agent.skills.<name>`；环境变量 **`AGENT_OS_LOADABLE_SKILL_PACKAGES`**；`get_agent` 向 `get_incremental_tools` 传入 `Settings`；示例包 **`sample_skill`**（`ping_sample_skill` 工具）与 `sample_skill.json` 配方。

### 历史新增（Asset Store 等）

- **Asset Store（案例库 / LanceDB）**：新增第四层“参考案例库”能力（整存整取、Dynamic Few-Shot，语感参考），设计见 `docs/ASSET_STORE.md`；`retrieve_ordered_context` 增加第④层，新增工具 `search_reference_cases`。
- **离线入库**：新增 CLI `agent-os-runtime asset-ingest <input>`（规则校验 + LLM gatekeeper + 特征抽取 + embedding + 写入）。
- **插件化开关**：新增 `AGENT_OS_ENABLE_ASSET_STORE` / `AGENT_OS_ASSET_STORE_PATH`、`AGENT_OS_ENABLE_HINDSIGHT`、`AGENT_OS_ENABLE_MEM0_LEARNING`。
- **依赖**：新增可选 extra `.[asset_store]`（包含 `lancedb`）。

## [0.6.0] - 2026-04-17

### 破坏性变更

- **主键改为 `skill_id`**：移除 **`AGENT_OS_PERSONA`**、**`--persona`** 与 **`Settings.agent_persona`**；CLI 使用 **`--skill`**；`get_agent(..., skill_id=...)`。
- **Manifest**：废弃 **`AGENT_OS_MANIFEST_PATH`**；改为 **`AGENT_OS_MANIFEST_DIR`** 目录扫描 + 包内置 **`data/skill_manifests/{skill_id}.json`** 注册表（`load_skill_manifest_registry`）；默认 skill：**`AGENT_OS_DEFAULT_SKILL_ID`**。
- **Graphiti / JSONL `group_id`**：由纯 `sanitize_group_id(client_id)` 改为 **`graphiti_group_id(client_id, skill_id)`**；**旧数据须按新键重新 ingest 或迁移**。

### 新增

- **`agent/skills/`**：**`get_incremental_tools(skill_id)`**（Phase 1 占位，返回空列表）；平台工具与增量工具在 `build_memory_tools` 内合并后再按 manifest 筛选。
- **`AgentManifestV1.agent_name`**：可选 Agno `Agent.name`（forge 模型已对齐可选字段）。
- **Web**：**`ChatIn.skill_id`**、**`AGENT_OS_WEB_SKILL_ID`**；记忆相关 API 增加可选 **`skill_id`** 以与对话 bundle 一致。

### 文档

- **`ENGINEERING.md` / `ARCHITECTURE.md` / `AGENTS.md` / `OPERATIONS.md`**：Skill 与复合 `group_id` 说明。

## [0.5.0] - 2026-04-17

> **0.6.0 起**：单文件 **`AGENT_OS_MANIFEST_PATH`** 及上述 **`doctor`** 检查已移除；以 **`AGENT_OS_MANIFEST_DIR`** 与注册表为准（见 **[0.6.0]**）。

### 新增

- **`AGENT_OS_MANIFEST_PATH`**（已于 0.6.0 废弃）：加载 **② forge** 导出的 `agent_config.json`（`AgentManifestV1`）：注入 `system_prompt`、按 `enabled_tools` 筛选工具、覆盖默认 `model`（可选）、展示 `handbook_version`。
- **`manifest_loader.py`**：与 forge 字段对齐的 Pydantic 模型（无运行时依赖 forge）。
- **`doctor`**（已于 0.6.0 调整）：曾检查 `AGENT_OS_MANIFEST_PATH` 文件是否存在。

### 修复

- **`retrieve_ordered_context`**：补全 `@tool` 装饰器（此前未注册为工具）。

## [0.4.0] - 2026-04-17

### 新增

- **MCP 探针（默认 fixture）**：包内 `resources/mcp_probe_default.json`；环境变量 **`AGENT_OS_MCP_PROBE_FIXTURE_PATH`** 可覆盖；Agent 工具 **`fetch_probe_context`**；可选 **`pip install -e ".[mcp]"`** 后运行 **`agent-os-runtime mcp-probe-server`** 或 **`python -m agent_os.mcp`**（stdio MCP，工具 `get_probe_snapshot`）。
- **端到端 Evaluator（规则门）**：`agent_os.evaluator.e2e`；CLI **`agent-os-runtime eval <case.json>`**（仅 Golden rules，无 LLM）；示例 `tests/core/fixtures/e2e_eval_case.json`。
- **离线领域知识（无 Neo4j）**：`agent_os.knowledge.jsonl_append`；CLI **`agent-os-runtime knowledge-append-jsonl -o path.jsonl --client-id X --text ...`**。
- **离线 Graphiti 写入（需 Neo4j + OpenAI）**：`knowledge/graphiti_ingest.py`；CLI **`agent-os-runtime graphiti-ingest episodes.json`**；**`--dry-run`** 仅校验 JSON；示例 `docs/examples/graphiti_episodes.example.json`。
- **`doctor`**：检查 **`AGENT_OS_MCP_PROBE_FIXTURE_PATH`**。

### 依赖

- 可选 **`[mcp]`**：`mcp` SDK（仅探针服务端与将来 MCP 客户端集成需要）。

### 文档（同步）

- `ARCHITECTURE.md`：探针、规则门、离线 ingest；修正「未实现」过时表述。
- `ENGINEERING.md`：目录树补充 `mcp/`、`graphiti_ingest`、`jsonl_append`、`resources/`。
- `README.md`、`.env.example`：新子命令与环境变量。
- 历史条目 `0.1.0` 中「后续阶段」说明已标注由后续版本实现。

## [0.3.2] - 2026-04-17

### 新增

- **`AGENT_OS_HANDOFF_MANIFEST_PATH` 注入 Agent**：`get_agent` 读取 `handbook_handoff.json` 摘要写入系统指令（条目数、校验通过/失败、时间、schema 引用）。
- **Golden rules（可选）**：环境变量 **`AGENT_OS_GOLDEN_RULES_PATH`** 指向 JSON 数组（`pattern` + `message` + 可选 `id`）；工具 **`check_delivery_text`**；示例 `data/golden_rules.example.json`。
- **记忆槽启发式工具**：**`suggest_memory_lane`**（不写入存储，辅助选择 `record_*`）。
- **`doctor`**：检查 `AGENT_OS_GOLDEN_RULES_PATH` 文件是否存在。

### 说明

- CI：仓库内 `.github/workflows/ci.yml` 跑 `pytest`（见工作流文件）。

## [0.3.1] - 2026-04-17

### 新增

- **`agent-os-runtime doctor`**：环境自检（OpenAI / Mem0 / Neo4j / graphiti / handoff / VIDEO_RAW_INGEST_ROOT）；`--strict`。
- **HTTP 重试**：`Mem0MemoryBackend` 与 Graphiti `search_` 路径使用 `agent_os.util.retry.retry_sync`（指数退避）。
- **配置**：`AGENT_OS_HANDOFF_MANIFEST_PATH`（元数据提示，可选）。

### 仓库外

- **`ops-stack/PIPELINE.md`**：①→②→③ 命令与变量说明。
- **`ops-knowledge`**：`ops-knowledge validate` / `manifest`（见该目录 README）。

## [0.3.0] - 2026-04-17

### 新增（阶段 c）

- **Hindsight**：`HindsightStore` 替代 stub；`feedback` / `lesson` 行；`MemoryController.search_hindsight`。
- **检索顺序**：工具 `retrieve_ordered_context`（Mem0 → Hindsight → Graphiti）；`search_past_lessons`。
- **AsyncReview**：`review/async_review.py`；CLI 退出时 `submit_and_wait` 写入教训；`AGENT_OS_ASYNC_REVIEW_*`；`--no-async-review`。
- **CLI**：`agent.run` 收集 transcript；`--task-id`；默认路径 `data/hindsight.jsonl`（`AGENT_OS_HISTORICAL_PATH`）。

### 破坏性变更

- 环境变量 `AGENT_OS_HISTORICAL_STUB_PATH` 仍兼容，优先使用 **`AGENT_OS_HISTORICAL_PATH`**；默认文件名改为 `data/hindsight.jsonl`。

## [0.2.0] - 2026-04-17

### 新增（阶段 b）

- **Graphiti 只读**：`GraphitiReadService` → `graphiti.search_`，租户 `group_id` 映射、`asyncio` 超时、BFS 深度可配。
- **JSONL 降级**：`AGENT_OS_KNOWLEDGE_FALLBACK_PATH`；示例 `docs/examples/knowledge_fallback.example.jsonl`。
- **Agent 工具**：`search_domain_knowledge`；CLI `--no-knowledge`。
- **可选依赖**：`pip install -e ".[graphiti]"`（`graphiti-core`）。

### 文档

- 更新 `ENGINEERING.md`、`ARCHITECTURE.md`、`OPERATIONS.md`。

## [0.1.0] - 2026-04-17

### 新增

- 阶段 **(a)** 垂直切片：**Agno Agent + Mem0 + 本地降级 + MemoryController + Hindsight JSONL 占位**。
- CLI：`python -m agent_os` 或安装后的 `agent-os-runtime`。
- 工程文档：`ENGINEERING.md`、`ARCHITECTURE.md`、`OPERATIONS.md`。

### 说明

- 初版范围说明；Graphiti 只读、AsyncReview、MCP/Evaluator 等已在 **0.2.0 起**各版本实现，见上方更新条目。

### 测试

- `tests/test_memory_controller.py`：`pytest`（`pyproject.toml` 中 `testpaths = ["tests"]`，避免收集 `.venv`）。
