from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ForgeSettings:
    """工坊运行时配置（环境变量）。"""

    openai_api_key: str | None
    openai_api_base: str | None
    dspy_lm_model: str
    default_handbook_version: str
    data_dir: Path

    @classmethod
    def from_env(cls) -> ForgeSettings:
        dd = os.getenv("OPS_FORGE_DATA_DIR", "data")
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_api_base=os.getenv("OPENAI_API_BASE"),
            dspy_lm_model=os.getenv("OPS_FORGE_DSPY_MODEL", "openai/gpt-4o-mini"),
            default_handbook_version=os.getenv("OPS_HANDBOOK_VERSION", "0.1.0"),
            data_dir=Path(dd),
        )
