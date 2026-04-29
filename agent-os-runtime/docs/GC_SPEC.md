# GC Spec

> 本文记录 Golden Case 的字段级断言口径。Stage 2 只落 artifact trace / context 收口所需的最小断言；后续 stage 按 `ARCHITECTURE.md` 3.5 累积强化。

## Stage 2 Artifact Trace / Context

| GC | 输入 | Stage 2 字段级断言（一行口径） |
| --- | --- | --- |
| GC1：artifact ref 不回灌原文 | 包含长 tool result、长用户 source 或显式 artifact refs 的业务写作任务 | prompt / history 出现 `<artifact ref=...>`；不出现长原文连续片段；`artifact_diagnostics.artifact_ref_count >= 1`；`artifact_diagnostics.artifact_chars > 0` |
| GC2：artifact diagnostics 可归因 | 同一轮包含显式 artifact refs 与 artifactized history | `/context --json` 输出 `artifact_diagnostics`；`pending_digest_count` 与 prompt 中 `digest_status="pending"` 数一致；`tool_result_artifactized_count` / `source_artifactized_count` 反映对应 artifactization；Markdown 包含 `Artifact Diagnostics` |
| GC3：lifecycle 命令不做破坏性 GC | 存在 active artifact、archived artifact 与 orphan artifact | `artifact list/show/archive` 只操作 ArtifactStore 原文层；`blob gc --orphan` 输出 `dry_run=true`；dry-run 后 orphan artifact 仍可 `get_artifact()` 读取 |

## Stage 3 Compact Schema

| GC | 输入 | Stage 3 字段级断言（一行口径） |
| --- | --- | --- |
| GC4：compact 后恢复任务目标 | 包含多轮用户约束、assistant 进展与 artifact refs 的 task/session | `CompactSummary.schema_version == "v2"`（Phase 9 起；v1 历史数据通过 `scripts/migrate_compact_v1_to_v2.py` 迁移）；`core.goal` / `core.constraints` / `core.progress` / `core.last_user_instruction` 非空；`skill_state` 允许 `null`（业务字段级断言推到 Stage 7 真实 skill 接入后追加，详见 [ARCHITECTURE.md](ARCHITECTURE.md) §3.5）|
| GC5：artifact refs 在 compact 中持续可追踪 | compact 前已有 artifact refs 或 pinned refs 的 task/session | `core.current_artifact_refs` / `core.pinned_refs` 由代码层写入，不由 LLM 编造；ContextBuilder rehydration 后 `/context` 输出 `compact_diagnostics.rehydrated=true` 与正确 `summary_version` |

## Stage 4 GC-Resume

| GC | 输入 | Stage 4 字段级断言（一行口径） |
| --- | --- | --- |
| GC6：隔天 resume 恢复工作面 | 已 compact 的主线 session 隔天 resume，compact 后仍有 uncompacted tail 与 artifact refs | `resume_diagnostics.connect_or_fork == "fork"`；`decision_reason` 包含 `session_not_recent` 或 `forced_fork`；`final_state.compact_summary != null`；`tail_message_count >= 1`；`current_artifact_ref_count >= 1`；`voice_pack_skipped=true` |
| GC7：分支对照隔离 | 从主线 session 执行 `/task branch` 后，分支继续 compact 为另一组 artifact refs | branch session `parent_session_id == source_session_id` 且 `branch_role == "branch"`；`tasks.current_main_session_id` 不变；main 与 branch 的 `CompactSummary.core.current_artifact_refs` 分别保留各自 refs，互不污染 |
| GC8：短 session 提前 resume 走 connect 路径 | recent + under budget 的短 session 执行 `/task resume`，并将 resume payload 注入 `/context` | `resume_diagnostics.connect_or_fork == "connect"`；`source_session_id == target_session_id`；`decision_reason == ["recent_session_under_budget"]`；tail history 纯文本投影可见；`/context` JSON / Markdown 均显示 `resume_diagnostics` |

## Baseline Trace 口径

Stage 2 Battle 6 与 Stage 4 Battle 5 分别补跑最小 observed baseline，用于确认 artifact trace / context integration / resume recovery 的字段可见性；下表数值用于回溯当次实现，不作为永久阈值：

| Baseline | 输入 | 关键输出 |
| --- | --- | --- |
| Trace 1：长 tool result artifactization | history 中 1 条超过阈值的 tool result，经 `ToolResultArtifactizer` 注入 `ContextBuilder` | `artifact_ref_count=1`；`pending_digest_count=0`；`artifact_chars=395`；`artifact_percent_of_prompt=0.3802`；`tool_result_artifactized_count=1`；`source_artifactized_count=0`；`current_user_source_artifactized=false` |
| Trace 2：pending artifact ref `/context` | 显式传入 1 条 `digest_status="pending"` 的 `ArtifactContextRef` | `artifact_ref_count=1`；`pending_digest_count=1`；`artifact_chars=152`；`artifact_percent_of_prompt=0.1546`；`tool_result_artifactized_count=0`；`source_artifactized_count=0`；`current_user_source_artifactized=false` |
| Trace 3：隔天 resume fork 恢复 | 已 compact 主线 session 在 `now + 31min` 后 resume，compact 后追加 1 条 tail，携带 artifact ref | `connect_or_fork=fork`；`decision_reason=["session_not_recent"]`；`tail_message_count=1`；`current_artifact_ref_count=1`；`voice_pack_skipped=true`；`final_state.compact_summary != null` |
| Trace 4：分支对照隔离 | `/task branch` 生成 branch session 后，main 与 branch 分别 compact 为 `artifact_main` / `artifact_branch` | branch `parent_session_id=s1`；`branch_role=branch`；`current_main_session_id=s1`；main refs=`["artifact_main"]`；branch refs=`["artifact_branch"]` |
| Trace 5：短 session connect + `/context` | recent 短 session resume 走 connect，并把 `task resume --json` payload 传入 `context-diagnose --resume-diagnostics-json` | `connect_or_fork=connect`；`source_session_id == target_session_id`；`decision_reason=["recent_session_under_budget"]`；`/context` JSON 含 `resume_diagnostics`；Markdown 含 `Resume Diagnostics` |

回归验证命令：

- `python -m pytest tests/core/test_task_memory.py tests/core/test_context_builder.py tests/core/test_cli.py tests/core/test_context_diagnostics.py tests/core/test_tool_result_artifactization.py tests/core/test_source_artifactization.py tests/core/test_artifact_store.py`
- `python -m ruff check src tests`

真实长任务 baseline 暂不固化为数据集；Stage 3 compact 落地后再把 GC1-3 与 compact schema 断言串成跨 stage case。
