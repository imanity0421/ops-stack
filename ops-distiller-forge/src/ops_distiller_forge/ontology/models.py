from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LineageMeta(BaseModel):
    """溯源：与 ① 产出、手册版本对齐。"""

    source_relpath: str = Field(..., description="相对批次根的路径，如 lesson1/lesson_merged.json")
    source_sha256: str | None = None
    handbook_version: str = "0.1.0"
    pipeline_version: str = Field(default="0.1.0", description="ops-distiller-forge 版本")
    ingested_at_utc: str = Field(default_factory=utc_now_iso)
    lesson_id: str | None = Field(None, description="业务侧课 id，可选")


class KnowledgePoint(BaseModel):
    """
    方法论母版（B）单条知识点。
    字段与方案讨论一致；后续可扩展 relations / ontology_path。
    """

    id: str = Field(..., min_length=8, description="稳定 id，建议 UUID")
    title: str
    theory_logic: str = Field("", description="底层逻辑：为什么")
    sop_steps: list[str] = Field(default_factory=list, description="可执行步骤")
    key_metrics: list[str] = Field(default_factory=list, description="衡量指标")
    anti_patterns: list[str] = Field(default_factory=list, description="避坑")
    case_reference: list[str] = Field(default_factory=list, description="案例引用摘要")
    metadata: LineageMeta
    cluster_key: str | None = Field(
        None,
        description="Reduce 阶段聚簇键；Map 阶段可空",
    )


class EpisodeRecord(BaseModel):
    """供 Graphiti add_episode 或 ops-agent graphiti-ingest 使用的一条记录。"""

    name: str
    body: str
    source_description: str = "ops_distiller_forge.projection"
    source: Literal["text", "message"] = "text"
    client_id: str = "demo_client"
    knowledge_point_id: str | None = None


class EpisodeBatchFile(BaseModel):
    """与 ops-agent docs/examples/graphiti_episodes.example.json 兼容的批结构。"""

    client_id: str = "demo_client"
    episodes: list[EpisodeRecord] = Field(default_factory=list)


class AgentManifestV1(BaseModel):
    """
    ③ Agno 侧「配方」：由 Loader 读取；工具在代码 tool_registry 中按 id 绑定。
    manifest_version 与 ops-agent 加载逻辑同步演进。
    """

    manifest_version: str = "1.0"
    handbook_version: str = "0.1.0"
    system_prompt: str = ""
    model: str | None = "gpt-4o-mini"
    temperature: float | None = 0.2
    enabled_tools: list[str] = Field(
        default_factory=lambda: [
            "retrieve_ordered_context",
            "search_domain_knowledge",
            "fetch_ops_probe_context",
        ],
        description="与 ops-agent 工具名对齐的字符串 id",
    )
    notes: str | None = None
    agent_name: str | None = Field(
        default=None,
        description="可选；Agno Agent.name，与 ops-agent manifest 对齐",
    )
