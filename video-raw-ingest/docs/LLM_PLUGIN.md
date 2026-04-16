# LLM 插件（可选 · OpenAI 兼容 API / 4zapi）

主流水线 **不依赖** LLM。本模块在 **`run` 完成之后**（或你已有 `lesson_merged.json` 时）按需调用 Chat Completions，用于：

| 子命令 | 作用 |
|--------|------|
| `llm ping` | 测试密钥与 `OPENAI_API_BASE` 是否可用 |
| `llm summarize <lesson_dir>` | 基于口播+画面文本生成**中文短摘要** → 默认 `llm_summary.md` |
| `llm suggest-issues <lesson_dir>` | 列出**可能的数据质量问题**（抽检辅助）→ 默认 `llm_quality_hints.md` |

## 配置

1. 复制仓库根 **`.env.example`** 为 **`.env`**，填写 **`OPENAI_API_KEY`**。  
2. 使用 **4zapi** 等第三方聚合时，**必须**设置 **`OPENAI_API_BASE=https://4zapi.com/v1`**（以平台文档为准）。  
3. 在控制台选用 **`OPENAI_CHAT_MODEL`**（模型 ID 以平台为准）。

加载顺序与 `video-asset-pipeline` 阶段 B 一致：

1. 仓库根 `.env`（若存在）  
2. 环境变量 **`RAW_INGEST_DOTENV`** 指向的额外文件  
3. 子命令 **`--env-file`**

若从已安装包路径无法定位仓库根，可设置 **`RAW_INGEST_REPO_ROOT`**；或在**仓库根目录**作为当前工作目录执行（cwd 下存在 `.env` 时优先用 cwd）。

## 命令示例

```bash
cd /root/video-raw-ingest
source .venv/bin/activate

python -m video_raw_ingest llm ping
python -m video_raw_ingest llm summarize /root/autodl-tmp/raw-ingest/某课/第01讲
python -m video_raw_ingest llm suggest-issues /root/autodl-tmp/raw-ingest/某课/第01讲 -o /tmp/hints.md
```

**注意**：摘要/质检会消耗 API 额度；长课 JSON 可能在 `suggest-issues` 中被截断（见 `llm/plugin.py` 实现）。
