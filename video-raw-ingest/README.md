# video-raw-ingest

**原始内容获取（至「结构合并」）**：从课程视频生成 **口播时间轴（WhisperX）** + **带时间戳的关键帧（内置抽帧，对齐 extract-video-ppt 思路）** + **MinerU 画面转 Markdown**，输出 **`lesson_merged.json` / `lesson_merged.md`** 与 **`validation_report.json`**。

- **不包含**：data-juicer 或其它文本清洗（请单独项目处理）。  
- **与 [video-asset-pipeline](https://github.com/imanity0421/video-asset-pipeline) 解耦**，路径习惯可对齐使用。

## 权威文档（长期维护）

| 文档 | 内容 |
|------|------|
| **[docs/OPERATIONS.md](docs/OPERATIONS.md)** | **操作手册**：从环境安装到单文件/批量命令、产出与排障 |
| **[docs/ENGINEERING.md](docs/ENGINEERING.md)** | **工程方案依据**：边界、架构、目录、Schema、设计决策、环境变量、版本记录 |
| [docs/AUTODL.md](docs/AUTODL.md) | AutoDL / Linux 部署与批量运行 |
| [docs/LLM_PLUGIN.md](docs/LLM_PLUGIN.md) | 可选 LLM 插件（4zapi / OpenAI 兼容）：`llm ping` / `summarize` / `suggest-issues` |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本变更摘要 |

变更流水线或 `lesson_merged.json` 结构时，请**同时**更新 `ENGINEERING.md` 与 `schema/lesson_merged.schema.json`。

## 环境要求

- Python 3.10+
- FFmpeg / ffprobe
- NVIDIA GPU + CUDA（推荐，用于 WhisperX 与 MinerU）
- 依赖：`pip install -e .`（见 `pyproject.toml`）
- **MinerU** 请按 [MinerU 官方文档](https://github.com/opendatalab/MinerU) 单独安装
- **可选 LLM**：复制 **`.env.example`** 为 **`.env`**，配置 `OPENAI_API_KEY` 与 **4zapi** 的 `OPENAI_API_BASE`（见 [docs/LLM_PLUGIN.md](docs/LLM_PLUGIN.md)）

```bash
export HF_ENDPOINT=https://hf-mirror.com   # 可选，国内加速模型下载
```

## 安装

```bash
cd video-raw-ingest
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e .
```

## 用法

```bash
python -m video_raw_ingest run /path/to/lesson.mp4 -o /path/to/out_dir
```

断点续跑（示例）：

```bash
python -m video_raw_ingest run lesson.mp4 -o out --skip-whisperx --skip-slides
```

更多参数：`python -m video_raw_ingest run --help`

重跑且输出目录非空时，使用 **`--replace`**（先写 staging，校验通过后再替换旧目录）或 **`--force-in-place`**（原地覆盖），见 [OPERATIONS.md](docs/OPERATIONS.md)。

默认 **`--keyframe-commit tail`**（静止后再保存，接近完整页）。若需「一变就截」可用 **`--keyframe-commit immediate`**。

可选 LLM：`python -m video_raw_ingest llm ping`（需 `.env`）。

## 输出物（摘要）

每课输出目录见 **[docs/ENGINEERING.md §3](docs/ENGINEERING.md)**；主交付物为 **`lesson_merged.json`**。

## 许可证

MIT — 见 [LICENSE](LICENSE)。第三方组件（WhisperX、MinerU 等）遵循其各自许可证。
