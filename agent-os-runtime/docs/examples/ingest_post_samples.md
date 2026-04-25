# `POST /ingest` 示例（P2-6）

运行 Web 示例：`python examples/web_chat_fastapi.py`，默认 `http://127.0.0.1:8787`（以启动参数为准）。

请求头建议带 **`X-Request-ID`**（可选，不传则服务端生成并在响应头 **`X-Request-ID`** 返回）。

## 1. `target=mem0_profile`（本地或 Mem0 画像层）

```http
POST /ingest HTTP/1.1
Content-Type: application/json
X-Request-ID: demo-mem0-1

{
  "target": "mem0_profile",
  "text": "用户偏好晚间 20:00 后接收关键进展。",
  "client_id": "demo_client",
  "mem_kind": "preference"
}
```

## 2. `target=hindsight`（任务反馈 → Hindsight JSONL）

```http
POST /ingest HTTP/1.1
Content-Type: application/json
X-Request-ID: demo-hs-1

{
  "target": "hindsight",
  "text": "用户反馈首屏前 5 秒信息过满。",
  "client_id": "demo_client",
  "skill_id": "default_agent"
}
```

## 3. `target=asset_store`（参考案例库 / LanceDB）

需 **`AGENT_OS_ENABLE_ASSET_STORE=1`**，且正文满足入库管线最小长度（默认约 200 字，见 `asset_ingest`）。无 **`lancedb`** 时接口会 500。

调试可设 **`AGENT_OS_INGEST_ALLOW_LLM=0`** 关闭裁判/特征 LLM（仅最小可检索字段），仍走合规与去重。

```http
POST /ingest HTTP/1.1
Content-Type: application/json
X-Request-ID: demo-asset-1

{
  "target": "asset_store",
  "text": "（此处替换为 ≥200 字、熵足够的整案正文，避免单字重复刷满）",
  "client_id": "demo_client",
  "skill_id": "default_agent"
}
```

## 生产检查项（路线图）

- **鉴权**：仅内网或 BFF **API Key / mTLS**，禁止公网裸奔。
- **限流**：按 `client_id` 或 IP 做配额，防刷库。
- **Payload 大小**：在网关限制 `Content-Length`。
