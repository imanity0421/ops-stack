# 变更日志

## [0.1.3] - 2026-04-17

- **文档**：`README.md`、`CONTRACT.md` 与 `dspy-stub`、工作区 `PIPELINE.md`、ops-agent 子命令/环境变量对齐。

## [0.1.2] - 2026-04-17

- **`ops-knowledge dspy-stub`**：无 LLM 的占位蒸馏（从 `lesson_merged.json` 生成摘要 JSON，供 DSPy 真蒸馏前联调）。

## [0.1.1] - 2026-04-17

- **测试**：`tests/fixtures/lesson_merged.schema.json` 供无 sibling `video-raw-ingest` 时跑通校验；`conftest` 优先使用该 fixture。
- **CI**：`.github/workflows/ci.yml`（Python 3.10 / 3.12，`pytest`）。

## [0.1.0] - 2026-04-17

- 首版：`ops-knowledge validate`、`ops-knowledge manifest`、CONTRACT 文档。
