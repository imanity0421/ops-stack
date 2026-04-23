from __future__ import annotations

import logging
import time
from typing import Callable, Tuple, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


def _transient_exceptions() -> Tuple[type[BaseException], ...]:
    out: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        import httpx

        out.extend([httpx.HTTPError, httpx.TransportError])
    except ImportError:
        pass
    return tuple(out)


TRANSIENT_EXCEPTIONS: Tuple[type[BaseException], ...] = _transient_exceptions()


def retry_sync(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 0.35,
    retry_on: Tuple[type[BaseException], ...] = TRANSIENT_EXCEPTIONS,
    label: str = "call",
) -> T:
    """对瞬时网络/传输错误做有限次重试（指数退避）。"""
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:
            if i == attempts - 1:
                logger.warning("%s 最终失败 (%s/%s): %s", label, i + 1, attempts, e)
                raise
            delay = base_delay_sec * (2**i)
            logger.info("%s 重试 %s/%s 前等待 %.2fs: %s", label, i + 1, attempts, delay, type(e).__name__)
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
