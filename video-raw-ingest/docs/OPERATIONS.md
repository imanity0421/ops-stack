# 操作手册：从环境到生成 `lesson_merged.json`

本文档描述**实际操作顺序**与**命令**；设计背景见 [ENGINEERING.md](./ENGINEERING.md)，云端路径见 [AUTODL.md](./AUTODL.md)。

---

## 1. 流水线在做什么（5 步）

| 顺序 | 步骤 | 说明 |
|------|------|------|
| 1 | 抽音频 | FFmpeg → `_work/audio_16k.wav` |
| 2 | 口播转写 | WhisperX → `whisperx/segments.json` |
| 3 | 关键帧 | 内置抽帧 → `slides/frames/*.jpg` + `slides/keyframes.json` |
| 4 | 画面转文字 | MinerU 逐帧 → `slides/mineru/…` + `slides/slides.json` |
| 5 | 合并 + 校验 | `lesson_merged.json` / `lesson_merged.md` + `validation_report.json` |

默认**串行**执行（避免 GPU 上多任务抢显存）。

---

## 2. 一次性环境准备

**Linux / AutoDL（推荐）**

```bash
cd /root
git clone <你的 GitHub 仓库 URL> video-raw-ingest
cd video-raw-ingest
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

按 [MinerU 官方文档](https://github.com/opendatalab/MinerU) 安装 MinerU（与 torch 冲突时可使用环境变量 `MINERU_PYTHON` 指向另一 venv 的 `python`）。

```bash
ffmpeg -version && ffprobe -version
export HF_ENDPOINT=https://hf-mirror.com   # 可选，国内拉模型
```

**Windows（本机开发）**

```powershell
cd D:\Coding\ops-stack\video-raw-ingest
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .
```

---

## 3. 单视频：完整命令

将课程视频放在任意路径（AutoDL 上建议数据盘，例如 `/root/autodl-tmp/…`）：

```bash
cd /path/to/video-raw-ingest
source .venv/bin/activate   # Windows 用 .venv\Scripts\activate
export HF_ENDPOINT=https://hf-mirror.com

python -m video_raw_ingest run /root/autodl-tmp/课程/第01讲.mp4
```

- **不指定 `-o`**：若存在 `/root/autodl-tmp`，默认输出到 **`/root/autodl-tmp/raw-ingest/<镜像子路径>/<视频主文件名>/`**（与 `RAW_INGEST_INPUT_ROOT` 配合可镜像目录结构，见 [ENGINEERING.md](./ENGINEERING.md) §6）。
- **重跑且目录里已有旧结果**：二选一  
  - **`--replace`**（推荐）：先写入临时 staging，**校验通过后再整体替换**旧目录；中断不会先删掉旧结果。  
  - **`--force-in-place`**：原地覆盖，中断可能留下半成品。  
  - 若输出目录**已非空**且两者都不加，命令会**拒绝运行**（防误覆盖）。
- **指定输出目录**：

```bash
python -m video_raw_ingest run /root/autodl-tmp/课程/第01讲.mp4 \
  -o /root/autodl-tmp/raw-ingest/课程/第01讲
```

带安全替换的重跑示例：

```bash
python -m video_raw_ingest run /root/autodl-tmp/课程/第01讲.mp4 \
  -o /root/autodl-tmp/raw-ingest/课程/第01讲 --replace
```

**成功标准**：终端无报错；同目录下 **`validation_report.json`** 中 `"status": "ok"`；主交付物 **`lesson_merged.json`** 存在。

**校验失败**：退出码为 `2`，报告见 `validation_report.json` 的 `errors`。

---

## 4. 常用参数（按需）

```bash
python -m video_raw_ingest run --help
```

摘录：

| 参数 | 含义 |
|------|------|
| `--similarity 0.6` | 关键帧灵敏度（越低越容易切出新页） |
| `--keyframe-commit` | 默认 **`tail`**（尾帧）；`immediate` 为「一变就截」 |
| `--keyframe-min-stable-seconds 2` | `tail`：连续稳定秒数（与 `--keyframe-inter-stability` 配合） |
| `--max-frames N` | 最多保留 N 张关键帧 |
| `--whisperx-model large-v2` | WhisperX 模型名 |
| `--device cuda` / `cpu` | 推理设备 |
| `--mineru-backend pipeline` | MinerU 后端（以官方为准） |
| `--mineru-fail-fast` | 任一帧 MinerU 失败即停止 |
| `--require-speech` | 硬校验：口播不得为空 |
| `--skip-whisperx` 等 | 断点续跑（需已有对应中间文件） |

---

## 5. 批量处理

脚本：**`tools/batch_ingest.sh`**（行为对齐 `video-asset-pipeline` 的 `batch_stage_a.sh`：递归、跳过产出目录、透传参数）。

**同一目录下多个视频**（默认仅当前一层）：

```bash
export RAW_INGEST_BATCH_INPUT=/root/autodl-tmp/课程合集
./tools/batch_ingest.sh
# 或
./tools/batch_ingest.sh /root/autodl-tmp/课程合集
```

**递归子目录**、并统一带 **安全替换**（示例）：

```bash
BATCH_RECURSE=1 ./tools/batch_ingest.sh /root/autodl-tmp/课程根 --replace
```

**遇错即停**（可选）：`BATCH_STOP_ON_FAIL=1 ./tools/batch_ingest.sh ...`

详见 [ENGINEERING.md §5.7](./ENGINEERING.md)。

---

## 5.1 可选 LLM（4zapi 等）

在 **`run` 成功产出 `lesson_merged.json` 后**，可按需配置 `.env` 并执行：

```bash
python -m video_raw_ingest llm ping
python -m video_raw_ingest llm summarize /path/to/lesson_out_dir
```

说明见 [LLM_PLUGIN.md](./LLM_PLUGIN.md)。

---

## 6. 产出在哪里、给下游什么

每课一个目录，**给清洗项目**的主文件：

- **`lesson_merged.json`**（必选）
- 可选：`lesson_merged.md`（人工浏览）

详见 [ENGINEERING.md §3](./ENGINEERING.md) 文件清单。

---

## 7. 故障排查（简表）

| 现象 | 建议 |
|------|------|
| 找不到 ffmpeg | 安装 FFmpeg 或设置 `FFMPEG_BIN` |
| WhisperX / CUDA 报错 | `nvidia-smi`；必要时 `--device cpu` 试跑 |
| MinerU 找不到 | 安装 MinerU 或设置 `MINERU_BIN` / `MINERU_PYTHON` |
| 校验失败 | 读 `validation_report.json`；检查帧路径是否被移动 |

更细的设计说明见 [ENGINEERING.md](./ENGINEERING.md)。
