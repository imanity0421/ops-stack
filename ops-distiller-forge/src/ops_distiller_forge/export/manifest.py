from __future__ import annotations

import json
from pathlib import Path

from ops_distiller_forge.ontology.models import AgentManifestV1


def write_agent_manifest(path: Path, manifest: AgentManifestV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_agent_manifest(path: Path) -> AgentManifestV1:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AgentManifestV1.model_validate(raw)
