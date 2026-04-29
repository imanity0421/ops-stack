# GC Spec

> 本文记录 Golden Case 的字段级断言口径。Stage 2 只落 artifact trace / context 收口所需的最小断言；后续 stage 按 `ARCHITECTURE.md` 3.5 累积强化。

## Stage 2 Artifact Trace / Context

| GC | 输入 | Stage 2 字段级断言（一行口径） |
| --- | --- | --- |
| GC1：artifact ref 不回灌原文 | 包含长 tool result、长用户 source 或显式 artifact refs 的业务写作任务 | prompt / history 出现 `<artifact ref=...>`；不出现长原文连续片段；`artifact_diagnostics.artifact_ref_count >= 1`；`artifact_diagnostics.artifact_chars > 0` |
| GC2：artifact diagnostics 可归因 | 同一轮包含显式 artifact refs 与 artifactized history | `/context --json` 输出 `artifact_diagnostics`；`pending_digest_count` 与 prompt 中 `digest_status="pending"` 数一致；`tool_result_artifactized_count` / `source_artifactized_count` 反映对应 artifactization；Markdown 包含 `Artifact Diagnostics` |
| GC3：lifecycle 命令不做破坏性 GC | 存在 active artifact、archived artifact 与 orphan artifact | `artifact list/show/archive` 只操作 ArtifactStore 原文层；`blob gc --orphan` 输出 `dry_run=true`；dry-run 后 orphan artifact 仍可 `get_artifact()` 读取 |

## Baseline Trace 口径

Battle 6 收口已补跑 2 个最小 observed baseline，用于确认 artifact trace / context integration 的字段可见性；下表数值用于回溯当次实现，不作为永久阈值：

| Baseline | 输入 | 关键输出 |
| --- | --- | --- |
| Trace 1：长 tool result artifactization | history 中 1 条超过阈值的 tool result，经 `ToolResultArtifactizer` 注入 `ContextBuilder` | `artifact_ref_count=1`；`pending_digest_count=0`；`artifact_chars=395`；`artifact_percent_of_prompt=0.3802`；`tool_result_artifactized_count=1`；`source_artifactized_count=0`；`current_user_source_artifactized=false` |
| Trace 2：pending artifact ref `/context` | 显式传入 1 条 `digest_status="pending"` 的 `ArtifactContextRef` | `artifact_ref_count=1`；`pending_digest_count=1`；`artifact_chars=152`；`artifact_percent_of_prompt=0.1546`；`tool_result_artifactized_count=0`；`source_artifactized_count=0`；`current_user_source_artifactized=false` |

回归验证命令：

- `python -m pytest tests/core/test_context_builder.py tests/core/test_cli.py tests/core/test_tool_result_artifactization.py tests/core/test_source_artifactization.py tests/core/test_artifact_store.py`
- `python -m ruff check src tests`

真实长任务 baseline 暂不固化为数据集；Stage 3 compact 落地后再把 GC1-3 与 compact schema 断言串成跨 stage case。
