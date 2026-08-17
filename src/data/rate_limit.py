"""线程安全的最小间隔限频器。

为批量并发分析提供供应商调用频率下限保护（客户端保守限速，
服务端限频仍由供应商积分/配额决定）。纯标准库，单进程线程级互斥。
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Blocking, thread-safe minimum-interval gate.

    Each ``acquire()`` call blocks until at least ``min_interval_s`` has
    elapsed since the previous call returned, which caps the request rate at
    roughly ``1 / min_interval_s`` calls per second across all threads.
    """

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last_ts = 0.0

    @property
    def min_interval_s(self) -> float:
        return self._min_interval_s

    def acquire(self) -> None:
        """Block until the next call may proceed."""
        if self._min_interval_s <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._last_ts + self._min_interval_s - now
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()
