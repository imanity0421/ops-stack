"""系统「宪法」：当多源信息冲突时，**组织最终回复**的优先级（写死、可测）。

与 ``retrieve_ordered_context`` 的**检索**顺序（Mem0→Hindsight→…）互补：检索层决定取什么进上下文；
本块决定在模型整合时的**效力和取舍**（红线 > 当轮用户 > …）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_os.manifest_loader import AgentManifestV1

# 与路线图 P1-3 及测试文档对齐；改文本须同步 ``docs/examples/constitutional_test_cases.md``。
MARKER = "【系统宪法·冲突解决序】"

CONSTITUTIONAL_CORE = f"""{MARKER}（多源信息冲突时，按以下**优先级**组织答案，不可颠倒）

1. **红线** — 硬合规与事实边界：不编造、不冒充官方或泄露用户/第三方隐私；须遵守每 skill 硬合规、Golden rules、可引用的**法规/平台条款**（若与工具检索结果一起出现，本层始终优先）。
2. **当轮显式用户指令** — 不触红线的，以**本轮**用户明确意图为准（含格式、风格、目标、侧重）。
3. **Hindsight 教训** — 历史复盘/反馈中对同类问题的纠偏（次于**当场**明确指令）。
4. **领域 SOP/规则** — Graphiti/配方手册/交付 handoff/经核准的表述模板等**可执行流程**。
5. **Asset 参考案例** — 语感、结构、段落组织参考；**不能**覆盖 1 或冒称事实。

**特别判定**
- 若用户要求**违反 1** 的交付文本/策略：须**拒绝**并说明原因，不得以「按用户来」执行。
- 若**仅**与纸面 SOP/案例习惯冲突、且**不触 1**，从 **2**（当轮用户）。
- 与 ``retrieve_ordered_context`` 的①②③④**检索**顺序无矛盾：先检索、再依本表整合。
"""


def build_constitutional_instruction_blocks(
    manifest: "AgentManifestV1 | None",
    *,
    enabled: bool,
) -> list[str]:
    """
    返回须置于**其它**系统指令**之前**的片段列表（可多项以便分段注入）。
    ``manifest.constitutional_prompt`` 用于 skill 在宪法骨架下的补充条款（**追加**在核心段之后）。
    """
    if not enabled:
        return []
    out = [CONSTITUTIONAL_CORE.strip()]
    if manifest is not None:
        extra = (manifest.constitutional_prompt or "").strip()
        if extra:
            out.append(f"【本 Skill 合宪补充】\n{extra}")
    return out
