from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from agent_os.agent.compact import SkillSchemaProvider


@dataclass
class SkillSchemaProviderRegistry:
    """Registry for SR-owned compact schema fragments."""

    _providers: dict[str, SkillSchemaProvider] = field(default_factory=dict)

    def register(self, skill_id: str, provider: SkillSchemaProvider) -> None:
        if not skill_id:
            raise ValueError("skill_id must not be empty")
        fragment = provider.get_compact_schema_fragment()
        if fragment is not None and (not isinstance(fragment, type) or not issubclass(fragment, BaseModel)):
            raise TypeError("skill schema fragment must be a pydantic BaseModel type")
        self._providers[skill_id] = provider

    def get_schema_fragment(self, skill_id: str) -> type[BaseModel] | None:
        provider = self._providers.get(skill_id)
        if provider is None:
            return None
        return provider.get_compact_schema_fragment()
