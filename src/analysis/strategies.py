"""User-extensible backtest strategies.

The built-in MA-cross strategy lives here, and — crucially — a *user override*
file can be placed at ``<user_data>/strategies/ma_cross.py`` to change the
signal rule or parameters **without touching the package source**. The file is
loaded at run time with :mod:`importlib`; if it is missing, malformed, or
raises during import, the engine logs a warning and falls back to the built-in
strategy so a user edit can never brick the backtester.

A user strategy file is a plain Python module that must define:

- ``NAME: str`` — strategy display name.
- ``DESCRIPTION: str`` — one-line human description.
- ``PARAMETERS: tuple[str, ...]`` — ordered parameter names.
- ``DEFAULTS: dict[str, int]`` — default values for those parameters.
- ``compute_signal(frame, params) -> pd.Series`` — given a prepared daily
  frame (columns ``trade_date/open/high/low/close/volume``, sorted) and a
  ``params`` mapping of the parameter values, return a 0/1 integer series
  (0 = flat / 1 = long) aligned with ``frame``.

The module docstring and the ``_TEMPLATE`` below document this contract so
users can copy the template as a starting point.
"""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from src.runtime_paths import user_data_root

SignalFunction = Callable[[pd.DataFrame, dict[str, Any]], pd.Series]

USER_STRATEGY_REL = Path("strategies") / "ma_cross.py"

# Template shown in the GUI strategy editor / created on first open.
STRATEGY_TEMPLATE = '''"""我的自定义回测策略（用户可编辑）。

合约说明（不要删除这些接口）：
- NAME / DESCRIPTION：名称与一句话说明。
- PARAMETERS：参与优化与展示的参数名（有序元组）。
- DEFAULTS：参数默认值。
- compute_signal(frame, params) -> pd.Series：
    frame 为已整理的日线 DataFrame（含 trade_date/open/high/low/close/volume，按日期升序）；
    返回与 frame 等长的 0/1 整数序列（0=空仓，1=持有），用 params 中的参数计算信号。
    信号在当日收盘确认、次日开盘成交（引擎统一处理，无需在此实现买卖）。

示例：把内置的双均线交叉改成 MA 金叉 + RSI 过滤（可选）。
"""

import numpy as np
import pandas as pd

NAME = "ma_cross_rsi"
DESCRIPTION = "双均线金叉 + RSI 超卖过滤（示例：用户可自行修改）"
PARAMETERS = ("ma_fast", "ma_slow", "rsi_period", "rsi_floor")
DEFAULTS = {"ma_fast": 10, "ma_slow": 30, "rsi_period": 14, "rsi_floor": 40}


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(period).mean()
    loss = (-delta.clip(upper=0.0)).rolling(period).mean()
    rs = gain / loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def compute_signal(frame: pd.DataFrame, params: dict) -> pd.Series:
    fast = frame["close"].rolling(int(params["ma_fast"])).mean()
    slow = frame["close"].rolling(int(params["ma_slow"])).mean()
    rsi = _rsi(frame["close"], int(params.get("rsi_period", 14)))
    floor = float(params.get("rsi_floor", 40))
    cross = (fast > slow) & fast.notna() & slow.notna()
    # 仅在 RSI 处于超卖/低位时才允许金叉买入（示例过滤逻辑）
    allow = rsi.fillna(0.0) < floor
    return (cross & allow).astype(int)
'''


@dataclass(frozen=True)
class Strategy:
    """A loaded, callable strategy with its parameter contract."""

    name: str
    description: str
    compute_signal: SignalFunction
    parameters: tuple[str, ...] = ("ma_fast", "ma_slow")
    defaults: dict[str, int] = field(
        default_factory=lambda: {"ma_fast": 20, "ma_slow": 60}
    )
    source: str = "builtin"
    file_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": list(self.parameters),
            "defaults": dict(self.defaults),
            "source": self.source,
            "file_path": str(self.file_path) if self.file_path else None,
        }


def builtin_strategy() -> Strategy:
    """Return the built-in MA-cross strategy."""

    def compute_signal(frame: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
        fast = frame["close"].rolling(
            int(params["ma_fast"]), min_periods=int(params["ma_fast"])
        ).mean()
        slow = frame["close"].rolling(
            int(params["ma_slow"]), min_periods=int(params["ma_slow"])
        ).mean()
        return ((fast > slow) & fast.notna() & slow.notna()).astype(int)

    return Strategy(
        name="ma_cross",
        description="双均线交叉（金叉买入/死叉卖出，T+1 开盘成交）",
        compute_signal=compute_signal,
    )


def user_strategy_file() -> Path:
    """Absolute path of the user-editable strategy file."""
    return user_data_root() / USER_STRATEGY_REL


def load_strategy() -> Strategy:
    """Load the user strategy if valid, else fall back to the built-in."""
    builtin = builtin_strategy()
    path = user_strategy_file()
    if not path.exists():
        return builtin

    user = _load_user_strategy(path)
    if user is None:
        logger.warning("用户策略文件无效，已回退到内置策略：{}", path)
        return builtin
    logger.info("使用用户自定义策略：{}（{}）", user.name, path)
    return user


def _load_user_strategy(path: Path) -> Strategy | None:
    try:
        module_name = f"_user_strategy_{abs(hash(str(path)))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        compute_signal = getattr(module, "compute_signal", None)
        if not callable(compute_signal):
            logger.warning("用户策略缺少可调用的 compute_signal(frame, params)")
            return None

        raw_parameters = getattr(module, "PARAMETERS", ("ma_fast", "ma_slow"))
        if isinstance(raw_parameters, str):
            parameters = tuple(
                p.strip() for p in raw_parameters.split(",") if p.strip()
            )
        else:
            parameters = tuple(str(p) for p in raw_parameters)
        if not parameters:
            parameters = ("ma_fast", "ma_slow")
        defaults_raw = getattr(module, "DEFAULTS", {}) or {}
        defaults = {str(k): int(v) for k, v in defaults_raw.items()}
        for name in parameters:
            if name not in defaults:
                defaults[name] = 20

        # validate compute_signal signature (frame, params)
        signature = inspect.signature(compute_signal)
        positional = [
            p
            for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) < 2:
            logger.warning("用户策略 compute_signal 至少需要 (frame, params) 两个参数")
            return None

        return Strategy(
            name=str(getattr(module, "NAME", path.stem)),
            description=str(getattr(module, "DESCRIPTION", "用户自定义策略")),
            compute_signal=compute_signal,
            parameters=parameters,
            defaults=defaults,
            source="user",
            file_path=path,
        )
    except Exception as exc:  # noqa: BLE001 - a broken user file must never crash
        logger.warning("加载用户策略失败（{}）：{}", type(exc).__name__, exc)
        return None


def save_user_strategy(source_code: str) -> Strategy:
    """Persist and reload the user strategy file atomically."""
    path = user_strategy_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(source_code, encoding="utf-8")
    temp.replace(path)
    strategy = load_strategy()
    if strategy.source != "user":
        raise ValueError("保存的策略文件无法加载，请检查语法与接口")
    return strategy


def reset_user_strategy() -> None:
    """Delete the user strategy file (fall back to the built-in strategy)."""
    path = user_strategy_file()
    if path.exists():
        path.unlink()


def strategy_source() -> str:
    """Return the current user strategy source (or the template if absent)."""
    path = user_strategy_file()
    if path.exists():
        return path.read_text(encoding="utf-8")
    return STRATEGY_TEMPLATE
