"""数据源调用监控 — 记录每次 API 调用的接口、参数、耗时、返回行数。"""

import functools
import time
from collections.abc import Callable

from loguru import logger


def monitor_call(func: Callable) -> Callable:
    """装饰器：监控数据源 API 调用。

    记录接口名称、参数、耗时、返回行数到日志。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 提取关键参数（跳过 self）
        call_args = args[1:] if args else ()
        arg_str = ", ".join(
            [str(a)[:50] for a in call_args]
            + [f"{k}={str(v)[:50]}" for k, v in kwargs.items()]
        )

        t0 = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - t0

            # 提取行数
            if hasattr(result, "__len__"):
                row_count = len(result)
            elif isinstance(result, dict):
                row_count = 1 if result else 0
            else:
                row_count = "?"

            logger.debug(
                f"[DS] {func.__name__}({arg_str}) -> {row_count} rows, {elapsed:.2f}s"
            )
            return result
        except Exception as e:
            elapsed = time.time() - t0
            logger.warning(
                f"[DS] {func.__name__}({arg_str}) -> ERROR after {elapsed:.2f}s: {e}"
            )
            raise

    return wrapper


class CallStats:
    """数据源调用统计。"""

    def __init__(self):
        self._calls: list[dict] = []

    def record(self, name: str, params: str, rows: int, elapsed: float, error: str = ""):
        self._calls.append({
            "name": name,
            "params": params,
            "rows": rows,
            "elapsed": elapsed,
            "error": error,
            "timestamp": time.time(),
        })

    def summary(self) -> dict:
        if not self._calls:
            return {"total": 0}

        errors = [c for c in self._calls if c["error"]]
        return {
            "total": len(self._calls),
            "errors": len(errors),
            "total_time": round(sum(c["elapsed"] for c in self._calls), 2),
            "by_method": self._count_by("name"),
        }

    def _count_by(self, key: str) -> dict:
        counts: dict[str, int] = {}
        for c in self._calls:
            counts[c[key]] = counts.get(c[key], 0) + 1
        return counts


# 全局实例
call_stats = CallStats()
