from __future__ import annotations

import zipfile
from pathlib import Path

from ops_agent.backup_data_core import copy_data_candidates_to_dir, run_local_data_backup


def test_run_local_data_backup_creates_zip(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "local_memory.json").write_text('{"users":{}}', encoding="utf-8")
    out = tmp_path / "out"
    path, added = run_local_data_backup(repo_root=repo, output_dir=out)
    assert path.is_file()
    assert "local_memory.json" in added
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert any(n.endswith("data/local_memory.json") for n in names)


def test_copy_candidates_empty_when_no_data_dir(tmp_path: Path) -> None:
    assert copy_data_candidates_to_dir(repo_root=tmp_path, dest_data=tmp_path / "d") == []
