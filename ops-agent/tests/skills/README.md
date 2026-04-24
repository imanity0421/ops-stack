# 按 Skill 拆分的专项测试（P3-7）

与公共用例隔离：各子目录自有 `fixtures/`，互不复用对方 JSON。

只跑某一类 skill 集：

```bash
cd ops-agent
python -m pytest -m skill_short_video -q
python -m pytest -m skill_business -q
```

引擎仍统一为 `ops_agent.evaluator.e2e.run_e2e_eval_file` / `run_e2e_eval_from_dict`（仅此一套 Golden 规则评测，不另起打分体系）。
