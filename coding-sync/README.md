# 跨机器开发同步（公司 / 家里）

本目录位于 **`ops-stack` 仓库内**，与 **①②③ 管线代码一起** `git pull` / `git push`，**无需单独仓库或云盘**（前提：两台机器只同步本仓库）。

用于记录：**改了什么、跑了什么命令、下一步做什么**，方便另一台电脑的 Cursor / 人类快速接续。

## 推荐工作流（与 ops-stack 同仓）

1. 在本机改代码 + 按需更新 `SESSION_LOG.md` 或 `runs.jsonl`（或运行 `log_session.py`）。
2. 收工前在 **`ops-stack` 根目录**：`git add` → `git commit` → `git push`。
3. 另一台机器：**先 `git pull`**，再读 `coding-sync/SESSION_LOG.md` 最近 1～2 节。

> 若你曾在工作区根目录另有 `coding-sync`，请改用本路径；旧位置已废弃。

### 不再需要的做法

- ~~在 `coding-sync` 里单独 `git init`~~（已与 ops-stack 合并）
- ~~仅靠云盘同步本目录~~（除非你不通过 Git 同步 ops-stack）

---

## 文件说明

| 文件 | 作用 |
|------|------|
| `SESSION_LOG.md` | **人类可读**：按日期/会话写摘要、待办、决策。 |
| `runs.jsonl` | **机器可读**：每次重要命令一行 JSON（测试、构建、迁移脚本）。 |
| `log_session.py` | 追加 SESSION 段落或写入一条 run（跨平台）。 |
| `log_session.ps1` | Windows 下快捷封装。 |

环境变量（可选）：`CODING_SYNC_MACHINE` = `office` / `home` 等，写入日志时自动带上机器名。

---

## 命令示例（在 `ops-stack` 根目录下）

```powershell
# 记一条会话摘要
python coding-sync\log_session.py session --title "ops-agent short_video 人设" --body "已改 factory/cli；回家跑 pytest"

# 记一次命令执行
python coding-sync\log_session.py run --cmd "pytest -q" --exit-code 0 --cwd "ops-agent"
```

或在 `coding-sync` 目录内：

```powershell
cd coding-sync
python log_session.py session --title "..." --body "..."
```

PowerShell：

```powershell
.\coding-sync\log_session.ps1 session -Title "..." -Body "..."
.\coding-sync\log_session.ps1 run -Cmd "pytest -q" -ExitCode 0
```

---

## 与 Cursor 的配合

**以 `ops-stack` 为工作区根打开**时，`.cursor/rules/coding-sync.mdc` 会提醒 Agent：在完成一批修改或结束会话前，更新本目录下的日志。

---

## 收工检查清单（可复制到 SESSION_LOG）

1. `git status`：未提交变更是否已说明或已提交。
2. 是否已 `git push`（另一台机器依赖此步）。
3. 下一台机器第一件事：`git pull`，再读 `SESSION_LOG.md`。
