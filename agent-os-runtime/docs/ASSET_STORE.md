# Asset Store / 案例库（CaseLibrary）设计稿

本文档定义 agent-os-runtime 在「全局层按 user 存储参考案例库」需求下的最终落地方案：**整存整取**、用于 **Dynamic Few-Shot（语感参考）**，与 Mem0/Hindsight/Graphiti 并列为第四类存储能力。

> 设计约束（来自产品/架构要求）
>
> - **Factory 不写数据库查询代码**：`agent/factory.py` 只负责创建 Agent、挂载工具与固定 instructions；所有 LanceDB 读写封装在 `agent_os/knowledge/asset_store.py`。
> - **运行时不做清洗**：LLM 清洗/特征提取/垃圾判定必须在 **Ingestion（入库）阶段**完成；Agent 对话时只做“秒查秒回”的检索与格式化输出。
> - **插件化**：无 Mem0/Hindsight/AssetStore 时可“裸跑”；可开关写入/自动学习/案例库能力。

---

## 1. 目标与非目标

### 目标
- 支持用户上传“个人案例库/作品集”，系统保存为 **整案资产**，用于生成时的“语感/结构/节奏”参考。
- 检索粒度为 **case-level**（一条记录对应一条完整案例），返回少量 top-k 作为 few-shot 参照。
- 全链路 **按 `client_id` 隔离**；可选按 `user_id` 隔离；可选按 `skill_id` 分区。

### 非目标
- 不做 Graph-RAG 的实体关系解释。
- 不在运行时进行长链路评估与清洗（避免对话延迟不可控）。
- 不保证在极长原文上进行全文 embedding（将通过特征抽取避免向量被主题稀释）。

---

## 2. 运行时架构落点（读路径）

### 2.1 工具化检索（与现有架构一致）
运行时通过 Agno 工具拉取上下文，不在 factory 拼接动态检索结果。

- 新增工具（建议）
  - `search_reference_cases(query: str) -> str`：检索案例库（Asset Store），返回 top-k 的“风格卡/摘要/片段”。
  - `retrieve_ordered_context(query: str) -> str`：扩展为四层：
    1. Mem0（画像/偏好）
    2. Hindsight（反馈/教训）
    3. Graphiti（领域知识）
    4. Asset Store（参考案例）

> 说明：factory 仅“挂工具”，不查库；检索发生在工具函数内部，由 `AssetStore` 封装实现。

---

## 3. 数据模型与存储 Schema（整存整取 + 向量只做特征）

### 3.1 核心原则
- **raw_content 不切片**：整篇案例作为 payload 保存。
- **embedding 不对 raw_content**：只对 `retrieval_text`（“摘要 + 风格指纹 + 标签”）做向量化，用于“写法/语感”相似度检索。
- **metadata 可过滤**：用结构化字段做硬过滤，避免仅靠向量相似度。

### 3.2 建议字段（Pydantic/表字段）
- 标识与隔离
  - `case_id: str`
  - `client_id: str`
  - `user_id: str | None`
  - `skill_id: str`
  - `source: str | None`

- 内容（注入与展示）
  - `raw_content: str`（整案原文，仅 payload）
  - `summary: str`（高密度摘要）
  - `style_fingerprint: str`（风格指纹：语气/节奏/结构/人设/镜头语言/句式等）
  - `key_excerpts: list[str]`（可选：少量关键片段）

- 检索相关
  - `tags: list[str]`（可枚举标签）
  - `platform: str | None`（通用含义：交付场景或使用环境）
  - `content_type: str | None`（通用含义：内容类型）
  - `duration_bucket: str | None`（通用含义：长度区间）
  - `retrieval_text: str`（用于 embedding）
  - `embedding: vector`（由 store 管理）

- 治理与风险
  - `status: Literal["accepted","quarantined","rejected"]`
  - `quality_score: float | None`
  - `risk_flags: list[str]`
  - `reject_reason: str | None`
  - `created_at: str`（ISO 时间）

---

## 4. Ingestion Pipeline（离线/入库阶段）

运行时不做清洗；因此必须在入库阶段完成“垃圾过滤 + 特征抽取 + embedding”。

### 4.1 三段式（最小可用骨架）
1. **Validator（规则）**：字数、乱码、重复、字段缺失、编码、非法结构。
2. **LLM Gatekeeper（裁判）**：是否符合业务范式、是否可迁移、是否存在明显风险；决定 `accepted/quarantined/rejected`。
3. **LLM Feature Extractor（特征提取）**：生成 `summary/style_fingerprint/tags/...` 并构造 `retrieval_text`。
4. **Embed + Store**：对 `retrieval_text` embedding 并写入 LanceDB。

### 4.2 CLI 形态（参考 graphiti-ingest / knowledge-append-jsonl）
- 新增 `agent-os-runtime asset-ingest <input>`：
  - 支持 JSON/JSONL（每行一个案例）与纯文本（单案例）
  - 参数携带 `client_id/user_id/skill_id`
  - 输出入库报告（accepted/quarantined/rejected 计数与原因摘要）

---

## 5. 插件化与开关策略

### 5.1 开关（建议环境变量）
- `AGENT_OS_ENABLE_ASSET_STORE`：是否启用案例库（读工具 + store 初始化）
- `AGENT_OS_ASSET_STORE_PATH`：本地 LanceDB 路径（或 URI）
- `AGENT_OS_ENABLE_HINDSIGHT`：是否启用 Hindsight（存储 + 工具）
- `AGENT_OS_ENABLE_MEM0_LEARNING`：是否允许工具写入 Mem0（关闭则不挂载 record 工具；读可保留）

### 5.2 Null Object 模式
- 未启用或未配置时，注入 `NullAssetStore`：
  - `search(...) -> []`
  - `ingest(...) -> ok/skipped`
  - 工具层可选择不挂载（更强隔离）

---

## 6. 与 Mem0 / Hindsight 的“pipeline”对齐说明

- **Mem0**（本仓库侧）目前主要是工具直写（`record_client_fact/preference`）+ 平台侧持久化；本仓库不含“从对话自动抽取写入 Mem0”的 ingestion pipeline。
- **Hindsight** 有明确的离线/退出时 pipeline：`AsyncReviewService` 在会话结束提炼 lessons 并写 JSONL；运行时 `record_task_feedback` 工具写 feedback。
- **Asset Store** 将比 Mem0/Hindsight 更强调入库治理：运行时只检索；入库阶段完成质量控制与特征抽取。

---

## 7. 去重、租户与「不加 hash 会怎样」

- **跨租户“串库”**主要靠检索时在内存中过滤 `client_id` / `skill_id` / `user_id` 作用域，不依赖 content hash。
- **强指纹 `content_hash` + `dedup_key`（SHA-256 合成）**解决的是：同一租户、同 skill、同用户/共享 scope 下**重复上传同一份或等价正文**时，不无限堆重复行。没有 hash 也可以做到不“串租”，但**无法廉价判断“这条是不是已经进过库”**。
- **近似去重**（可选 `AGENT_OS_ASSET_NEAR_DEDUP_L2_MAX`）：正文略改但 `retrieval_text` 极近时，用向量 L2 拦截重复；不设环境变量则只做强指纹去重。

## 8. 硬合规（每 skill 一份规则）

- 环境变量 `AGENT_OS_SKILL_COMPLIANCE_DIR`，文件 `<skill_id>.json`，与 `AGENT_OS_GOLDEN_RULES_PATH` 同结构（`pattern` + `message`）。
- **asset-ingest**：在调用 LLM 裁判前对**原文**做合规校验，未通过则 `rejected`。
- **对话生成**：当目录存在时，挂载工具 `check_skill_compliance_text`（与入库同源），与 `check_delivery_text` 可并用。

## 9. 回退与清库（不做线上人工审队列时）

- `agent-os-runtime asset-rm --case-id <id>` 删除单条。
- `agent-os-runtime asset-rm --client-id <c> --skill <s> --all-skill` 删除该 tenant 下该 skill 的全部案例行（**危险**，用于你描述的「大量垃圾入库后一次性回退」）。

**检索时 `user_id` 未传**（`None`）：只返回 **`user_id` 为空**的共享案例，避免同租户多用户间互串私有案例。若需「整租户所有用户可见」，应在产品上显式用单独机制（例如只存 `user_id` 为空的共享库）。

