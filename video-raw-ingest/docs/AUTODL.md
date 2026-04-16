# AutoDL / Linux：部署与运行 `video-raw-ingest`

## 1. 目录约定

| 用途 | 路径 | 说明 |
|------|------|------|
| 代码与虚拟环境 | **`/root/video-raw-ingest`** | 与数据盘分离；`.venv` 放于此 |
| 视频与产出 | **`/root/autodl-tmp/…`** | 大文件放数据盘；默认输出根为 **`/root/autodl-tmp/raw-ingest/`** |

不要把仓库克隆到 `autodl-tmp`（关机可能清空，以平台说明为准）。

---

## 2. 首次安装

```bash
cd /root
git clone <你的仓库 URL> video-raw-ingest
cd video-raw-ingest
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

**MinerU** 请按 [MinerU 官方文档](https://github.com/opendatalab/MinerU) 安装（可能与 torch 版本有关；冲突时用 `MINERU_PYTHON` 指向独立 venv）。

确认 FFmpeg：

```bash
ffmpeg -version
ffprobe -version
# apt-get install -y ffmpeg   # Debian/Ubuntu 示例
```

国内模型下载建议：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

---

## 3. 单文件处理

```bash
cd /root/video-raw-ingest
source .venv/bin/activate
export HF_ENDPOINT=https://hf-mirror.com

python -m video_raw_ingest run /root/autodl-tmp/课程/第01讲.mp4
```

显式输出目录：

```bash
python -m video_raw_ingest run /root/autodl-tmp/课程/第01讲.mp4 \
  -o /root/autodl-tmp/raw-ingest/课程/第01讲
```

常用参数见 `python -m video_raw_ingest run --help`。

---

## 4. 批量（shell）

```bash
cd /root/video-raw-ingest
source .venv/bin/activate
chmod +x tools/batch_ingest.sh
export RAW_INGEST_BATCH_INPUT=/root/autodl-tmp/课程合集
./tools/batch_ingest.sh
```

- 默认只扫描**当前一层**；**递归**：`BATCH_RECURSE=1 ./tools/batch_ingest.sh /root/autodl-tmp/根`  
- **透传参数**（如 `--replace`）：`./tools/batch_ingest.sh /path --replace`  
- 不扫描输入树内的 **`raw-ingest/`** 及位于输入下的 **`RAW_INGEST_OUTPUT_ROOT`**（与 `video-asset-pipeline` 的 batch 逻辑一致）  
- 详见 [ENGINEERING.md §5.7](./ENGINEERING.md) 与 [OPERATIONS.md §5](./OPERATIONS.md)

---

## 5. RTX 5090 与 GPU

- WhisperX：默认 `--device cuda`；无 CUDA 时自动退回 CPU（极慢）。
- MinerU：默认使用其 GPU 后端；纯 CPU 可尝试 `--mineru-backend pipeline`（以 MinerU 文档为准）。

自检：

```bash
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

---

## 6. 产出交接给「清洗项目」

将每课目录中的 **`lesson_merged.json`**（及可选 `lesson_merged.md`）同步到清洗服务器；**不要**依赖仅存在于 AutoDL 数据盘的路径——清洗侧应只依赖 JSON 内相对路径或自行拷贝 `slides/` 资源。
