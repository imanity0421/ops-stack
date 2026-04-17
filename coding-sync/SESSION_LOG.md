# 开发会话日志

> **约定**：每次重要开发段落或换机器前，在下方追加一节；另一台电脑先读最近 1～2 节再动手。  
> 机器名可通过环境变量 `CODING_SYNC_MACHINE` 区分（如 `office` / `home`）。  
> **本目录已迁入 `ops-stack/coding-sync/`，与仓库同步。**

---

## 模板（复制后填写）

**日期**：YYYY-MM-DD  
**机器**：office / home（或 `CODING_SYNC_MACHINE`）  
**主要仓库/分支**：例如 `ops-stack` / `main`  

**本轮做了什么**（列表）：

- 

**运行过的关键命令**（可选，详细见 `runs.jsonl`）：

- 

**未竟 / 下一台机器先做**：

- 

**备注**（环境差异、密钥、路径）：

- 

---

## 2026-04-17T11:54:45+08:00 | LAPTOP-VPIF7FP8

**标题**：机制初始化

新增 coding-sync 目录、log_session 脚本与 Cursor 规则 alwaysApply。

---

## 2026-04-17 | 迁移

**标题**：迁入 ops-stack 仓库

`coding-sync` 自工作区根目录迁入 `ops-stack/coding-sync/`，便于与唯一同步仓库 `ops-stack` 一并 pull/push。
