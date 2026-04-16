# 变更记录

格式基于实际发布；与 `pyproject.toml` / `video_raw_ingest.__version__` 对齐。

## 0.2.2

- **抽帧**：`--keyframe-commit` **默认改为 `tail`**（尾帧）；需要「首变即存」时用 `--keyframe-commit immediate`

## 0.2.1

- **抽帧**：新增 `--keyframe-commit tail`，在画面相对上一关键帧大变后等待「相邻采样稳定」再落盘；当时默认仍为 `immediate`

## 0.2.0

- **`run`**：输出目录非空时默认拒绝覆盖；新增 **`--replace`**（staging 后校验再替换）、**`--force-in-place`**
- **批量**：`tools/batch_ingest.sh` 对齐 `batch_stage_a`（`BATCH_RECURSE`、跳过 `raw-ingest` 与输入下的输出根、透传参数、`BATCH_STOP_ON_FAIL`）
- **可选 LLM 插件**：`llm ping` / `summarize` / `suggest-issues`（OpenAI 兼容 API，如 4zapi）；`.env.example`
- **依赖**：`openai`、`python-dotenv`
- **文档**：`docs/LLM_PLUGIN.md`；`ENGINEERING` / `OPERATIONS` / `AUTODL` 更新

## 0.1.0

- 首版：`run` 流水线（WhisperX + 关键帧 + MinerU + 结构合并 + 校验）、Schema、`docs/ENGINEERING.md` / `AUTODL.md` / `OPERATIONS.md`
