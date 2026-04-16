# video-raw-ingest 工程说明（方案依据与长期维护）

本文档描述 **设计目标、数据流、模块职责、目录约定、依赖与运维**，作为本仓库的**权威工程说明**；变更流水线行为时请同步更新本文档与 `README.md`。

---

## 1. 定位与边界

### 1.1 目标

从**单节课程视频**产出「原始多模态文本」的**结构合并**结果，供下游 **data-juicer 或其它清洗项目** 消费：

- **口播**：WhisperX 转写 + 字级/段级时间对齐（输出以段为主）。
- **画面**：内置关键帧抽取（对齐 `extract-video-ppt`/`evp` 思路）→ 每帧 **MinerU** 转 Markdown。
- **合并**：按时间线把 `speech` 与 `visual` 并列写入 `lesson_merged.json` / `lesson_merged.md`，**不做语义合并**（语义合并属于后续「蒸馏」步骤）。

### 1.2 明确不包含

- data-juicer 或任何「清洗后终稿」
- 向量库 / RAG / Agent / DSPy
- 对「烂课」自动救回（无 PPT、镜头乱晃等）——由业务侧弃课或人工筛选

---

## 2. 总体架构

```mermaid
flowchart LR
  V[视频]
  W[16k WAV]
  X[WhisperX]
  S[关键帧 + keyframes.json]
  M[MinerU 逐帧]
  J[lesson_merged.json / .md]
  R[validation_report.json]
  V --> W --> X
  V --> S --> M
  X --> J
  M --> J
  J --> R
```

**默认串行执行**（避免 GPU 上 WhisperX 与 MinerU 同时占显存）。需要并行时应在不同进程/不同机器拆分。

---

## 3. 目录与产物

### 3.1 单课输出目录（`run` 默认）

以 `<out_dir>/` 为根（例如 `/root/autodl-tmp/raw-ingest/课程/第01讲/`）：

| 路径 | 说明 |
|------|------|
| `_work/audio_16k.wav` | FFmpeg 抽取的单声道 16kHz PCM |
| `whisperx/segments.json` | 口播段 + 模型元数据 |
| `whisperx/raw_aligned.json` | WhisperX 对齐后的原始 JSON（排障） |
| `slides/frames/*.jpg` | 关键帧图片 |
| `slides/keyframes.json` | 关键帧元数据（含 `timestamp_sec`、`frame_relpath`） |
| `slides/slides.json` | 每帧 MinerU 结果汇总（便于断点续跑 `--skip-mineru`） |
| `slides/mineru/NNNN/` | MinerU 各帧输出目录（以工具实际结构为准） |
| **`lesson_merged.json`** | **主交付物**：结构合并 |
| **`lesson_merged.md`** | 人类可读并列视图 |
| **`validation_report.json`** | Schema + 硬规则校验结果 |

### 3.2 `lesson_merged.json` 约定

- **`schema_version`**：当前为 `"1.0"`；破坏性变更时递增并在本文档记录迁移说明。
- **`speech.empty` / `visual.empty` / `flags.*`**：便于下游区分「合法空」（如静音段）与数据异常（由校验报告进一步说明）。
- **`merged.timeline`**：按 `start_sec` 排序的并列事件（`speech` / `visual`），**非语义重写**。

JSON Schema 见：

- 包内：`src/video_raw_ingest/lesson_merged.schema.json`（随包分发）
- 仓库副本：`schema/lesson_merged.schema.json`

---

## 4. 模块说明

| 模块 | 职责 |
|------|------|
| `paths.py` | `RAW_INGEST_*` 环境变量与输出镜像规则（对齐 `video-asset-pipeline` 的 AutoDL 习惯） |
| `ffmpeg_util.py` | `ffprobe`、时长、视频 FPS 近似、`ffmpeg` 抽 WAV |
| `slide_extract.py` | 每秒采样 + BGR 直方图相关度；默认 **`tail`（尾帧）**；可选 `immediate`（首变帧）；见 `--keyframe-commit` |
| `whisperx_run.py` | WhisperX 转写 + 对齐；无语音段时跳过 align |
| `mineru_run.py` | 子进程调用 `mineru`（或 `MINERU_PYTHON -m mineru`）；聚合 `*.md` |
| `merge.py` | 生成 `lesson_merged.*` |
| `validate.py` | `jsonschema` + 路径存在性等硬规则；写 `validation_report.json` |
| `output_layout.py` | `--replace` 时先写入 `*.ingest-staging.<pid>`，**校验通过后再删除旧目录并整体替换** |
| `llm/` | 可选 OpenAI 兼容 API（如 4zapi）：`ping` / `summarize` / `suggest-issues` |
| `cli.py` | `run`（含安全替换）、`llm` 子命令 |

---

## 5. 关键设计决策（维护时需知）

### 5.1a 关键帧提交：`tail`（默认）与 `immediate`

- **`tail`（默认）**：检测到相对上一关键帧「开始大变」后**先不落盘**，用**最新采样**作候选；当**连续若干秒**内「与上一秒采样」足够相似（画面已静止）时再保存，更接近**整页出完后的尾帧**。参数：`--keyframe-inter-stability`、`--keyframe-min-stable-seconds`、`--keyframe-max-transition-sec`。
- **`immediate`**：当前采样帧与**上一张已保存关键帧**的相似度 **首次** 低于阈值时**立刻**保存。对**逐段渐显**的 PPT 往往偏早（未出全）；适合硬切、或明确要省时间、不等静止的场景。

### 5.1 为何内置抽帧而非直接调用 `evp`

PyPI `extract-video-ppt` 的 `evp` 在生成 PDF 后会删除临时 JPG，**不保留**带时间戳的逐帧资产，难以直接对接 MinerU。本仓库抽帧算法与 `wudududu/extract-video-ppt` **同类**（每秒采样 + 帧间相似度），并**持久化**帧图与 `keyframes.json`。

### 5.2 时间戳与 VFR

抽帧使用 **ffprobe 推断的 FPS** 计算 `timestamp_sec = frame_index / fps`。对 **VFR（可变帧率）** 视频，该值为**近似**；若课程源高度 VFR，应在业务侧转码为 CFR 或在后续版本改为基于 `CAP_PROP_POS_MSEC` 的采样（需评估性能）。

### 5.3 MinerU 与 PyTorch 版本

WhisperX 与 MinerU 可能依赖不同 **torch** 版本。推荐：

- 在 AutoDL 上使用官方文档推荐的 **CUDA + torch** 组合；
- 若冲突，可用 **`MINERU_PYTHON`** 指向另一虚拟环境中的 Python，仅用于执行 `python -m mineru`（见 `mineru_run.resolve_mineru_command`）。

### 5.4 MinerU 失败策略

- 默认：**记录 `mineru_error` 并继续**下一帧。
- `--mineru-fail-fast`：任一帧失败则停止（适合调试）。

### 5.5 校验策略

- **Schema**：`lesson_merged.schema.json`。
- **硬规则**：帧文件存在性、`duration_sec` 符号、可选 `--require-speech` / `--require-visual-text`。
- **退出码**：校验失败时 CLI 返回 **2**（与「参数/文件错误」区分）。

### 5.6 安全替换旧输出（对齐「先成功后删旧」）

- 若目标输出目录**已存在且非空**，且未指定 `--replace` / `--force-in-place`，`run` **拒绝覆盖**并提示，避免误覆盖。
- **`--replace`（推荐重跑）**：全程写入同级临时目录 `<stem>.ingest-staging.<pid>`，**仅当**流水线结束且 `validation_report.json` 为 `ok` 时，`shutil.rmtree` 删除旧 `<out_dir>`，再把 staging **整体移入**为 `<out_dir>`。中断或校验失败时**删除 staging**，旧目录保持不变。
- **`--force-in-place`**：直接在原 `<out_dir>` 写入（与旧版「覆盖写」类似），**中断可能留下半成品**，仅当你明确接受风险时使用。

### 5.7 批量处理（对齐 `video-asset-pipeline` 的 `batch_stage_a.sh` 习惯）

- 脚本：`tools/batch_ingest.sh`。
- **`BATCH_RECURSE=1`**：递归子目录查找视频。
- **跳过扫描**：输入树下的 `raw-ingest/`（若存在）、以及位于输入树内的 **`RAW_INGEST_OUTPUT_ROOT`**（避免把产出当输入）。
- **透传参数**：`"$@"` 原样传给每条 `python -m video_raw_ingest run`（例如 `--replace`、`--whisperx-model large-v3`）。
- **`BATCH_STOP_ON_FAIL=1`**：任一条失败立即退出；默认记录 `FAIL` 并继续下一条，最后若有失败则**非零退出**。
- 每条子进程设置 **`RAW_INGEST_INPUT_ROOT=<扫描根>`**，以便单课输出镜像相对子路径。

### 5.8 可选 LLM 插件（4zapi 等 OpenAI 兼容端）

- **不参与** ASR/抽帧/MinerU 主链路；用于连接自检、对 `lesson_merged.json` 生成**摘要**或**质量提示**，便于人工或下游快速浏览。
- 配置与 `video-asset-pipeline` 阶段 B 一致：`.env` 中 **`OPENAI_API_KEY`**、**`OPENAI_API_BASE`**（第三方聚合如 4Z 填 `https://4zapi.com/v1`）、**`OPENAI_CHAT_MODEL`**。
- 额外加载：`RAW_INGEST_DOTENV`、`--env-file`；仓库根定位优先 **`RAW_INGEST_REPO_ROOT`**，否则若 **cwd** 下存在 `.env` 则用 cwd（详见 `llm/env_loader.py`）。
- 子命令见 [LLM_PLUGIN.md](./LLM_PLUGIN.md)。

---

## 6. 环境变量一览

| 变量 | 含义 |
|------|------|
| `RAW_INGEST_OUTPUT_ROOT` | 输出根目录 |
| `RAW_INGEST_INPUT_ROOT` / `RAW_INGEST_BATCH_INPUT` | 输入根，用于镜像子路径 |
| `RAW_INGEST_REPO_OUTPUT` | `1` 时强制输出到仓库内 `output/` |
| `HF_ENDPOINT` | Hugging Face 镜像（如 `https://hf-mirror.com`） |
| `FFMPEG_BIN` / `FFPROBE_BIN` | 覆盖可执行路径 |
| `MINERU_BIN` | MinerU 可执行文件路径 |
| `MINERU_PYTHON` | 用于 `python -m mineru` 的解释器 |
| `MINERU_BACKEND` | 传给 MinerU 的 `-b`（可被 CLI `--mineru-backend` 覆盖） |
| `OPENAI_API_KEY` / `OPENAI_API_BASE` / `OPENAI_CHAT_MODEL` | 可选 LLM 插件（见 §5.8、[LLM_PLUGIN.md](./LLM_PLUGIN.md)） |
| `RAW_INGEST_DOTENV` | 在仓库 `.env` 之后再加载的额外 env 文件 |
| `RAW_INGEST_REPO_ROOT` | 显式指定仓库根（查找 `.env`）；一般可省略 |

---

## 7. 版本与变更记录

| 版本 | 说明 |
|------|------|
| 0.1.0 | 首版：run 流水线、Schema、硬校验、AutoDL 文档 |
| 0.2.0 | `--replace` / `--force-in-place`；批量脚本对齐 stage_a；可选 LLM 插件与 `.env.example` |
| 0.2.1 | `--keyframe-commit tail`：渐显 PPT 用尾帧（静止后再保存） |
| 0.2.2 | **默认** `keyframe-commit=tail`；`immediate` 需显式指定 |

**维护约定**：修改 `merged` 结构或默认行为时，请：

1. 更新 `lesson_merged.schema.json`（包内 + `schema/` 副本保持一致）  
2. 更新本文档与 `README.md`  
3. 在上方版本表追加一行  

---

## 8. 相关文档

- [OPERATIONS.md](./OPERATIONS.md) — 操作步骤与命令  
- [AUTODL.md](./AUTODL.md) — 云端实例上的克隆、venv、批量脚本  
- [LLM_PLUGIN.md](./LLM_PLUGIN.md) — LLM 子命令与配置  
- [CHANGELOG.md](./CHANGELOG.md) — 版本变更摘要  
- 上游项目参考：`video-asset-pipeline`（仅路径/习惯参考，无代码依赖）
