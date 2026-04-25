# 数据面备份与 Mem0（P3-8）

## 本地文件类（本仓库内）

使用脚本（在仓库 `agent-os-runtime/` 根目录执行）：

```bash
python scripts/backup_data.py
```

默认在 `backups/` 下生成 **`agent_os_data_YYYYMMDD_HHMMSS.zip`**，内含当前存在的：

- `data/hindsight.jsonl`
- `data/local_memory.json`
- `data/agno_session.db`（Agno 会话 Sqlite）
- `data/asset_store.lancedb/`（LanceDB 目录）

**不会**打包 `.env` 与任何密钥文件；请勿将 zip 提交到 Git。

自定义输出目录：

```bash
python scripts/backup_data.py --output-dir D:/archives/agent-os-runtime
```

## Mem0 托管（SaaS）

Mem0 侧用户记忆**无**本仓库专用「假导出 API」。运维方式：

1. **有官方导出/控制台**：以 [Mem0 官方文档](https://docs.mem0.ai/) 与当前套餐说明为准，在控制台或官方 API 导出/迁移。
2. **无自动化导出时**：在数据治理 SOP 中约定**周期性**人工导出或留档，并记录**责任人与周期**；不以本仓库脚本**假装**已对接 Mem0 全量备份。

若仅使用 **本地 JSON 后端**（未配置 `MEM0_API_KEY`），则主体画像在 `data/local_memory.json`，已由 `backup_data.py` 覆盖。

## 恢复

- 解压 zip 后，将 `data/` 下文件按路径覆盖到目标环境（停机后操作），再启动进程。
- Mem0 云端恢复仅能通过官方途径，不在此仓库实现自动回灌。
