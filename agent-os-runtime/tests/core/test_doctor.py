import json
from pathlib import Path

from agent_os.config import Settings
from agent_os.doctor import run_doctor
from agent_os.knowledge.graphiti_reader import GraphitiReadService


def test_doctor_strict_no_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert run_doctor(strict=True) == 1


def test_doctor_non_strict(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert run_doctor(strict=False) == 0


def test_doctor_ok_with_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert run_doctor(strict=True) == 0


def test_settings_invalid_numeric_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OS_SESSION_HISTORY_MAX_MESSAGES", "not-an-int")
    monkeypatch.setenv("AGENT_OS_SNAPSHOT_EVERY_N_TURNS", "not-an-int")
    monkeypatch.setenv("AGENT_OS_TASK_SUMMARY_MAX_CHARS", "not-an-int")

    s = Settings.from_env()

    assert s.session_history_max_messages == 20
    assert s.snapshot_every_n_turns == 5
    assert s.task_summary_max_chars == 800


def test_graphiti_invalid_numeric_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OS_GRAPHITI_SEARCH_TIMEOUT_SEC", "bad")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_MAX_RESULTS", "bad")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_BFS_MAX_DEPTH", "bad")

    svc = GraphitiReadService.from_env(None)

    assert svc._timeout_sec == 20.0
    assert svc._max_results == 12
    assert svc._bfs_max_depth == 2


def test_doctor_graphiti_entitlements_valid_file(tmp_path: Path, monkeypatch, capsys) -> None:
    p = tmp_path / "ent.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "global_allowed_skill_ids": ["default_agent"],
                "client_entitlements": {"c1": ["default_agent"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", str(p))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert run_doctor(strict=True) == 0
    out = capsys.readouterr().out
    assert "Graphiti 权限文件结构合法" in out


def test_doctor_graphiti_entitlements_invalid_structure_warns(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    p = tmp_path / "ent_bad.json"
    p.write_text(
        json.dumps({"version": "x", "client_entitlements": {"c1": "not_list"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", str(p))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert run_doctor(strict=True) == 0
    err = capsys.readouterr().err
    assert "Graphiti 权限文件结构不合法" in err


def test_doctor_warns_when_web_admin_enabled_without_token(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_OS_WEB_ENABLE_ADMIN_API", "1")
    monkeypatch.delenv("AGENT_OS_WEB_ADMIN_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_OS_WEB_ADMIN_API_TOKENS", raising=False)
    assert run_doctor(strict=True) == 0
    err = capsys.readouterr().err
    assert "未配置 AGENT_OS_WEB_ADMIN_API_TOKEN" in err


def test_doctor_warns_when_entitlements_store_is_not_file(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_STORE", "postgres")
    assert run_doctor(strict=True) == 0
    err = capsys.readouterr().err
    assert "当前仅支持 file 后端" in err


def test_doctor_invalid_settings_env_returns_fail(monkeypatch, capsys) -> None:
    monkeypatch.setenv("AGENT_OS_MEMORY_POLICY_MODE", "bad-mode")

    assert run_doctor(strict=False) == 1
    err = capsys.readouterr().err
    assert "配置环境变量无效" in err


def test_doctor_graphiti_entitlements_bad_utf8_warns(tmp_path: Path, monkeypatch, capsys) -> None:
    p = tmp_path / "ent_bad.json"
    p.write_bytes(b"\xff\xfe\x00")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", str(p))

    assert run_doctor(strict=True) == 0
    err = capsys.readouterr().err
    assert "Graphiti 权限文件 JSON 无效" in err


def test_doctor_graphiti_entitlements_oserror_warns(tmp_path: Path, monkeypatch, capsys) -> None:
    p = tmp_path / "ent.json"
    p.write_text("{}", encoding="utf-8")
    orig = Path.read_text

    def boom(self: Path, *args, **kwargs) -> str:
        if self == p:
            raise OSError("locked")
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", str(p))

    assert run_doctor(strict=True) == 0
    err = capsys.readouterr().err
    assert "Graphiti 权限文件 JSON 无效" in err


def test_doctor_handoff_oserror_warns(tmp_path: Path, monkeypatch, capsys) -> None:
    p = tmp_path / "handoff.json"
    p.write_text("{}", encoding="utf-8")
    orig = Path.read_text

    def boom(self: Path, *args, **kwargs) -> str:
        if self == p:
            raise OSError("locked")
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_OS_HANDOFF_MANIFEST_PATH", str(p))

    assert run_doctor(strict=True) == 0
    err = capsys.readouterr().err
    assert "handoff 清单 JSON 无效" in err
