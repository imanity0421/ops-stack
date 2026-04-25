"""P1-4 交付物契约：与 manifest ``output_mode`` 对齐的 Pydantic 输出模型，供 Agno ``output_schema`` 使用。"""

from __future__ import annotations

from typing import Any, Type

from pydantic import BaseModel, Field, field_validator

from agent_os.manifest_loader import AgentManifestV1

STRUCTURED_V1 = "structured_v1"


class PlanStructuredV1(BaseModel):
    """
    策划/提纲类交付：提纲与长文**分离**，避免单段超深 JSON。

    模型返回中 ``body_markdown`` 可承载长正文；主字段保持可解析的短结构。
    """

    title: str = Field(..., min_length=1, description="策划标题或主命题")
    outline: list[str] = Field(
        ...,
        min_length=1,
        description="分条提纲，非空；每条宜为一句可执行要点",
    )
    key_messages: list[str] = Field(
        default_factory=list,
        description="需重复强调的关键信息（可空）",
    )
    body_markdown: str = Field(
        default="",
        description="长文正文用 Markdown 放本字段，勿塞入过深的 JSON 树",
    )

    @field_validator("outline", mode="before")
    @classmethod
    def _coerce_outline(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v] if v.strip() else []
        if v is None:
            return []
        return list(v)


def resolve_structured_output_model(
    manifest: AgentManifestV1 | None,
) -> Type[BaseModel] | None:
    """
    当 ``output_mode == structured_v1`` 时，按 ``output_schema_version`` 返回内置 Pydantic 模型；未知版本返回 None（不挂 schema，避免静默错误）。
    """
    if manifest is None:
        return None
    mode = (manifest.output_mode or "").strip()
    if mode != STRUCTURED_V1:
        return None
    ver = (manifest.output_schema_version or "1.0").strip()
    if ver == "1.0":
        return PlanStructuredV1
    return None
