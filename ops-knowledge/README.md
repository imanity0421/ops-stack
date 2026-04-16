# ops-knowledge（② 管线占位）

在 **① `video-raw-ingest`**（目录名可在 **`ops-stack/ops-stack.toml`** 中配置）与 **③ `ops-agent`** 之间，提供**可提前完成**的衔接能力：

- **校验** `lesson_merged.json` 是否符合官方 Schema（引用 **①** 仓库内 `schema/lesson_merged.schema.json`）。
- **生成 handoff 清单** `handbook_handoff.json`（制品路径、校验状态、指纹），供后续 DSPy 蒸馏与 ops-agent 读入版本信息。

**占位蒸馏**：`ops-knowledge dspy-stub` 可在无 LLM 下从 `lesson_merged.json` 生成联调用 JSON。完整 DSPy 蒸馏与 Graphiti 入库仍由你方 **DSPy 项目 / 离线作业**（或 `ops-agent graphiti-ingest`）完成；本包不替代真炼丹。

## 权威文档

| 文档 | 内容 |
|------|------|
| [docs/CONTRACT.md](docs/CONTRACT.md) | ①→②→③ 契约与环境变量 |

## 安装

```bash
cd ops-knowledge
pip install -e ".[dev]"
```

## 用法

```bash
# 校验单课 merged JSON（--schema 或 VIDEO_RAW_INGEST_ROOT 或 CI 使用 tests/fixtures/lesson_merged.schema.json）
ops-knowledge validate path/to/lesson_merged.json

# 为某输出目录生成 handoff 清单（递归查找 lesson_merged.json）
ops-knowledge manifest --ingest-root path/to/course_out --output handbook_handoff.json

# 无 LLM：占位蒸馏 JSON（联调 DSPy 前）
ops-knowledge dspy-stub path/to/lesson_merged.json -o distill_stub.json
```

## 许可证

MIT
