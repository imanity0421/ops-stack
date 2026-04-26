from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data/graphiti_entitlements.json")
_DEFAULT_AUDIT_PATH = Path("data/graphiti_entitlements_audit.jsonl")
_DEFAULT_LOCK_TIMEOUT_SEC = 5.0
_DEFAULT_AUDIT_MAX_BYTES = 2 * 1024 * 1024
_DEFAULT_AUDIT_MAX_FILES = 10
_DEFAULT_AUDIT_RETENTION_DAYS = 30
_ATOMIC_REPLACE_RETRIES = 8
_ATOMIC_REPLACE_SLEEP_SEC = 0.02


class EntitlementsRevisionConflictError(RuntimeError):
    """乐观并发冲突：expected_revision 与当前文件 revision 不一致。"""

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(f"revision 冲突：expected={expected} actual={actual}")
        self.expected = expected
        self.actual = actual


def _parse_skill_list(raw: Any) -> set[str]:
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s:
            out.add(s)
    return out


def _atomic_replace_with_retry(src: Path, dst: Path) -> None:
    last_err: OSError | None = None
    for i in range(_ATOMIC_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            # Windows 下偶发被短暂占用，短退避后重试。
            time.sleep(_ATOMIC_REPLACE_SLEEP_SEC * (i + 1))
        except OSError as e:
            last_err = e
            break
    if last_err is not None:
        raise last_err


def _lock_path_for(path: Path) -> Path:
    suffix = f"{path.suffix}.lock" if path.suffix else ".lock"
    return path.with_suffix(suffix)


def _lock_timeout_sec() -> float:
    raw = (os.getenv("AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_LOCK_TIMEOUT_SEC
    try:
        v = float(raw)
    except ValueError:
        logger.warning(
            "AGENT_OS_GRAPHITI_FILE_LOCK_TIMEOUT_SEC=%r 非法，回退默认 %.1f",
            raw,
            _DEFAULT_LOCK_TIMEOUT_SEC,
        )
        return _DEFAULT_LOCK_TIMEOUT_SEC
    return max(0.1, v)


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        logger.warning("%s=%r 非法，回退默认 %s", name, raw, default)
        return default
    if min_value is not None and v < min_value:
        logger.warning("%s=%r 小于最小值 %s，回退默认 %s", name, raw, min_value, default)
        return default
    return v


def _warn_unsupported_store_env() -> None:
    raw = (os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_STORE") or "file").strip().lower()
    if raw and raw != "file":
        logger.warning(
            "AGENT_OS_GRAPHITI_ENTITLEMENTS_STORE=%r 已降级为可选历史配置；当前运行时使用 file 后端",
            raw,
        )


@contextmanager
def _exclusive_file_lock(lock_path: Path, *, timeout_sec: float) -> Any:
    """
    跨进程互斥锁（Windows: msvcrt.locking / POSIX: fcntl.flock）。

    注意：锁文件本身不会删除，便于后续复用。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = lock_path.open("a+b")
    start = time.monotonic()
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if (time.monotonic() - start) >= timeout_sec:
                    raise TimeoutError(f"获取文件锁超时: {lock_path}")  # noqa: TRY301
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:  # pragma: no cover - defensive logging
        logger.warning("Graphiti entitlements 文件解析失败: %s (%s)", path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("Graphiti entitlements 文件根节点不是对象: %s", path)
        return None
    return data


def validate_entitlements_document(obj: Any) -> list[str]:
    """校验权限文档结构与字段类型，返回错误列表（空表示通过）。"""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["root 必须是 object"]

    if "version" in obj and not isinstance(obj.get("version"), int):
        errs.append("version 必须是整数")
    if "revision" in obj and not isinstance(obj.get("revision"), int):
        errs.append("revision 必须是整数")

    if "global_allowed_skill_ids" in obj and not isinstance(
        obj.get("global_allowed_skill_ids"), list
    ):
        errs.append("global_allowed_skill_ids 必须是字符串数组")
    elif isinstance(obj.get("global_allowed_skill_ids"), list):
        for idx, v in enumerate(obj.get("global_allowed_skill_ids") or []):
            if not isinstance(v, str):
                errs.append(f"global_allowed_skill_ids[{idx}] 必须是字符串")

    if "client_entitlements" in obj and not isinstance(obj.get("client_entitlements"), dict):
        errs.append("client_entitlements 必须是对象（client_id -> [skill]）")
    elif isinstance(obj.get("client_entitlements"), dict):
        for k, v in obj.get("client_entitlements", {}).items():
            if not isinstance(k, str):
                errs.append("client_entitlements 的 key 必须是字符串")
                continue
            if not isinstance(v, list):
                errs.append(f"client_entitlements[{k!r}] 必须是字符串数组")
                continue
            for idx, item in enumerate(v):
                if not isinstance(item, str):
                    errs.append(f"client_entitlements[{k!r}][{idx}] 必须是字符串")
    return errs


def _normalize_entitlements_document(obj: dict[str, Any]) -> dict[str, Any]:
    errs = validate_entitlements_document(obj)
    if errs:
        logger.warning(
            "Graphiti entitlements 结构不合法，将尽量按兼容模式读取: %s", "; ".join(errs)
        )
    raw_map = obj.get("client_entitlements")
    c_map: dict[str, list[str]] = {}
    if isinstance(raw_map, dict):
        for k, v in raw_map.items():
            cid = str(k).strip()
            if not cid:
                continue
            c_map[cid] = sorted(_parse_skill_list(v))
    rev_raw = obj.get("revision", 0)
    try:
        rev = int(rev_raw)
    except (TypeError, ValueError):
        rev = 0
    rev = max(0, rev)
    return {
        "version": 1,
        "revision": rev,
        "global_allowed_skill_ids": sorted(_parse_skill_list(obj.get("global_allowed_skill_ids"))),
        "client_entitlements": c_map,
    }


@dataclass(frozen=True)
class GraphitiEntitlements:
    """Graphiti 权限解析器：优先持久化文件，env 仅作为兜底。"""

    global_allowed: set[str] | None
    client_map: dict[str, set[str]]
    env_global_allowed: set[str] | None
    env_client_map: dict[str, set[str]]

    @classmethod
    def from_sources(cls, *, path: Path | None = None) -> GraphitiEntitlements:
        _warn_unsupported_store_env()
        p = path or Path(os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", "") or _DEFAULT_PATH)
        file_global: set[str] | None = None
        file_map: dict[str, set[str]] = {}

        obj = _load_json_object(p)
        if obj is not None:
            if "global_allowed_skill_ids" in obj:
                file_global = _parse_skill_list(obj.get("global_allowed_skill_ids"))
            raw_map = obj.get("client_entitlements")
            if isinstance(raw_map, dict):
                for k, v in raw_map.items():
                    cid = str(k).strip()
                    if not cid:
                        continue
                    file_map[cid] = _parse_skill_list(v)

        env_client_map: dict[str, set[str]] = {}
        raw_map = os.getenv("AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON")
        if raw_map:
            try:
                env_obj = json.loads(raw_map)
                if isinstance(env_obj, dict):
                    for k, v in env_obj.items():
                        cid = str(k).strip()
                        if not cid:
                            continue
                        env_client_map[cid] = _parse_skill_list(v)
            except Exception:
                logger.warning("AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON 无法解析，忽略该配置")

        raw_global = os.getenv("AGENT_OS_GRAPHITI_ALLOWED_SKILL_IDS")
        env_global: set[str] | None = None
        if raw_global:
            env_global = {x.strip() for x in raw_global.split(",") if x.strip()}

        return cls(
            global_allowed=file_global,
            client_map=file_map,
            env_global_allowed=env_global,
            env_client_map=env_client_map,
        )

    def allows(self, client_id: str, skill_id: str) -> bool:
        cid = (client_id or "").strip()
        sid = (skill_id or "").strip()
        if not sid:
            return False

        if cid in self.client_map:
            allowed = self.client_map[cid]
            return "*" in allowed or sid in allowed
        if self.global_allowed is not None and len(self.global_allowed) > 0:
            return "*" in self.global_allowed or sid in self.global_allowed
        if self.global_allowed is not None and len(self.global_allowed) == 0:
            return False

        if cid in self.env_client_map:
            allowed = self.env_client_map[cid]
            return "*" in allowed or sid in allowed
        if self.env_global_allowed is None:
            return True
        return "*" in self.env_global_allowed or sid in self.env_global_allowed


def load_entitlements_file(path: Path) -> dict[str, Any]:
    """读取（不存在时返回空模板）并规范化 Graphiti 权限文件。"""
    _warn_unsupported_store_env()
    obj = _load_json_object(path) or {}
    return _normalize_entitlements_document(obj)


def save_entitlements_file(path: Path, data: dict[str, Any]) -> None:
    _warn_unsupported_store_env()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(path)
    timeout = _lock_timeout_sec()
    with _exclusive_file_lock(lock_path, timeout_sec=timeout):
        tmp_path = path.with_name(
            f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{int(time.time() * 1000)}"
        )
        try:
            doc = dict(data)
            if not isinstance(doc.get("revision"), int):
                doc["revision"] = 0
            tmp_path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            _atomic_replace_with_retry(tmp_path, path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass


def update_entitlements_file(
    path: Path,
    *,
    mutator: Callable[[dict[str, Any]], None],
    expected_revision: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    在同一把文件锁内完成：读取 -> revision 校验 -> 变更 -> revision+1 -> 原子落盘。

    返回 ``(before, after)``。若 revision 不匹配抛 ``EntitlementsRevisionConflictError``。
    """
    _warn_unsupported_store_env()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(path)
    timeout = _lock_timeout_sec()
    with _exclusive_file_lock(lock_path, timeout_sec=timeout):
        current = load_entitlements_file(path)
        actual = int(current.get("revision", 0) or 0)
        if expected_revision is not None and int(expected_revision) != actual:
            raise EntitlementsRevisionConflictError(expected=int(expected_revision), actual=actual)

        before = json.loads(json.dumps(current, ensure_ascii=False))
        after = json.loads(json.dumps(current, ensure_ascii=False))
        mutator(after)
        after["revision"] = actual + 1

        tmp_path = path.with_name(
            f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{int(time.time() * 1000)}"
        )
        try:
            tmp_path.write_text(
                json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            _atomic_replace_with_retry(tmp_path, path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
        return before, after


def graphiti_entitlements_audit_path() -> Path:
    raw = os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_AUDIT_PATH


def _audit_max_bytes() -> int:
    return _env_int(
        "AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_BYTES", _DEFAULT_AUDIT_MAX_BYTES, min_value=0
    )


def _audit_max_files() -> int:
    return _env_int(
        "AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_MAX_FILES", _DEFAULT_AUDIT_MAX_FILES, min_value=1
    )


def _audit_retention_days() -> int:
    return _env_int(
        "AGENT_OS_GRAPHITI_ENTITLEMENTS_AUDIT_RETENTION_DAYS",
        _DEFAULT_AUDIT_RETENTION_DAYS,
        min_value=0,
    )


def _audit_rotated_path(base: Path, idx: int) -> Path:
    return base.with_name(f"{base.name}.{idx}")


def _rotate_audit_if_needed(base: Path) -> None:
    max_bytes = _audit_max_bytes()
    if max_bytes <= 0:
        return
    try:
        if (not base.exists()) or base.stat().st_size < max_bytes:
            return
    except OSError:
        return

    max_files = _audit_max_files()
    oldest = _audit_rotated_path(base, max_files)
    if oldest.exists():
        try:
            oldest.unlink()
        except OSError:
            pass
    for i in range(max_files - 1, 0, -1):
        src = _audit_rotated_path(base, i)
        dst = _audit_rotated_path(base, i + 1)
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError:
                pass
    try:
        os.replace(base, _audit_rotated_path(base, 1))
    except OSError:
        pass


def _apply_audit_retention(base: Path) -> None:
    days = _audit_retention_days()
    if days <= 0:
        return
    cutoff = time.time() - float(days) * 86400.0
    candidates = [base]
    max_files = _audit_max_files()
    for i in range(1, max_files + 1):
        candidates.append(_audit_rotated_path(base, i))
    for p in candidates:
        if not p.exists():
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                p.unlink()
            except OSError:
                pass


def append_entitlements_audit(
    *,
    action: str,
    actor: str,
    source: str,
    entitlements_path: Path,
    before: dict[str, Any],
    after: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """追加一条权限变更审计日志（JSONL）。"""
    audit_path = graphiti_entitlements_audit_path()
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(audit_path)
    timeout = _lock_timeout_sec()
    row = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor": actor,
        "source": source,
        "entitlements_path": str(entitlements_path),
        "before": before,
        "after": after,
        "metadata": metadata or {},
    }
    with _exclusive_file_lock(lock_path, timeout_sec=timeout):
        _rotate_audit_if_needed(audit_path)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _apply_audit_retention(audit_path)


class GraphitiEntitlementsProvider:
    """
    权限配置提供器：带热加载与缓存失效能力。

    触发重载条件（任一满足）：
    - 首次读取（无缓存）
    - 权限文件 mtime 变化
    - env 兜底配置变化
    - 缓存 TTL 到期（默认 2 秒）
    """

    def __init__(self, *, path: Path | None = None, cache_ttl_sec: float = 2.0) -> None:
        _warn_unsupported_store_env()
        self._path = path or Path(
            os.getenv("AGENT_OS_GRAPHITI_ENTITLEMENTS_PATH", "") or _DEFAULT_PATH
        )
        self._cache_ttl_sec = max(0.0, float(cache_ttl_sec))
        self._lock = threading.Lock()
        self._cached: GraphitiEntitlements | None = None
        self._last_loaded_monotonic: float = 0.0
        self._last_mtime_ns: int | None = None
        self._last_env_sig: tuple[str, str] = ("", "")

    @property
    def path(self) -> Path:
        return self._path

    def _mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return None

    @staticmethod
    def _env_sig() -> tuple[str, str]:
        return (
            os.getenv("AGENT_OS_GRAPHITI_ALLOWED_SKILL_IDS", ""),
            os.getenv("AGENT_OS_GRAPHITI_CLIENT_ENTITLEMENTS_JSON", ""),
        )

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._last_loaded_monotonic = 0.0
            self._last_mtime_ns = None
            self._last_env_sig = ("", "")

    def get(self) -> GraphitiEntitlements:
        now = time.monotonic()
        mtime = self._mtime_ns()
        env_sig = self._env_sig()
        with self._lock:
            should_reload = False
            if self._cached is None:
                should_reload = True
            elif mtime != self._last_mtime_ns:
                should_reload = True
            elif env_sig != self._last_env_sig:
                should_reload = True
            elif self._cache_ttl_sec <= 0:
                should_reload = True
            elif (now - self._last_loaded_monotonic) >= self._cache_ttl_sec:
                should_reload = True

            if should_reload:
                self._cached = GraphitiEntitlements.from_sources(path=self._path)
                self._last_loaded_monotonic = now
                self._last_mtime_ns = mtime
                self._last_env_sig = env_sig

            assert self._cached is not None
            return self._cached
