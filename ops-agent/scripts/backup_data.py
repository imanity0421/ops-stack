#!/usr/bin/env python3
"""见 ``ops_agent.backup_data_core``；在仓库根执行： ``python scripts/backup_data.py``。"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证可 import ops_agent（需在 ops-agent 根下执行，或已 pip install -e .）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from ops_agent.backup_data_core import backup_main

if __name__ == "__main__":
    raise SystemExit(backup_main())
