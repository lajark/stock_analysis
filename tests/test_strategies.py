"""用户可编辑策略机制测试 — 内置回退 / 保存 / 重置 / 接入回测与优化。

使用 STOCK_ANALYSIS_HOME 隔离用户数据目录，避免触碰真实 .env 与策略文件。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.backtest import BacktestSpec, optimize_ma_cross, run_backtest
from src.analysis.strategies import (
    STRATEGY_TEMPLATE,
    builtin_strategy,
    load_strategy,
    reset_user_strategy,
    save_user_strategy,
    strategy_source,
    user_strategy_file,
)

USER_CODE = """\
NAME = "custom"
DESCRIPTION = "自定义双均线"
PARAMETERS = ("fast", "slow")
DEFAULTS = {"fast": 5, "slow": 20}


def compute_signal(frame, params):
    fast = frame["close"].rolling(int(params["fast"])).mean()
    slow = frame["close"].rolling(int(params["slow"])).mean()
    return ((fast > slow) & fast.notna() & slow.notna()).astype(int)
"""


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_ANALYSIS_HOME", str(tmp_path))
    return tmp_path


def _make_daily(prices: np.ndarray) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=len(prices))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.full(len(prices), 1_000_000.0),
        }
    )


def test_builtin_strategy_default(isolated) -> None:
    strategy = load_strategy()
    assert strategy.source == "builtin"
    assert strategy.name == "ma_cross"
    assert strategy.parameters == ("ma_fast", "ma_slow")
    assert strategy.defaults["ma_fast"] == 20


def test_save_and_load_user_strategy(isolated) -> None:
    strategy = save_user_strategy(USER_CODE)
    assert strategy.source == "user"
    assert strategy.name == "custom"
    assert strategy.parameters == ("fast", "slow")
    assert user_strategy_file().exists()
    assert "自定义" in strategy_source()


def test_load_prefers_user_strategy(isolated) -> None:
    save_user_strategy(USER_CODE)
    strategy = load_strategy()
    assert strategy.source == "user"
    assert strategy.name == "custom"


def test_reset_restores_builtin(isolated) -> None:
    save_user_strategy(USER_CODE)
    reset_user_strategy()
    strategy = load_strategy()
    assert strategy.source == "builtin"
    assert not user_strategy_file().exists()


def test_broken_user_file_falls_back(isolated) -> None:
    path = user_strategy_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def broken(:\n  return\n", encoding="utf-8")
    strategy = load_strategy()
    assert strategy.source == "builtin"


def test_save_broken_strategy_raises(isolated) -> None:
    with pytest.raises(ValueError):
        save_user_strategy("def broken(:\n  return\n")


def test_run_backtest_uses_user_strategy(isolated) -> None:
    save_user_strategy(USER_CODE)
    peak = np.concatenate([np.linspace(30, 70, 120), np.linspace(70, 40, 120)])
    result = run_backtest(_make_daily(peak), spec=BacktestSpec(initial_cash=100_000))
    # 用户策略参数为 fast=5/slow=20，交易次数应 > 0
    assert result.trade_count >= 1
    assert "custom" in result.strategy_version
    assert result.parameters["fast"] == 5


def test_optimize_supports_user_strategy_params(isolated) -> None:
    save_user_strategy(USER_CODE)
    peak = np.concatenate([np.linspace(30, 70, 120), np.linspace(70, 40, 120)])
    result = optimize_ma_cross(
        _make_daily(peak),
        {"fast": [3, 5, 10], "slow": [20, 60]},
        objective="sharpe",
        strategy=load_strategy(),
    )
    assert {"fast", "slow"} == set(result.selected_parameters)
    assert len(result.candidates) >= 1


def test_builtin_backtest_version_unchanged(isolated) -> None:
    """内置路径必须保持既有版本号契约（ma-cross-v1）。"""
    peak = np.concatenate([np.linspace(30, 70, 120), np.linspace(70, 40, 120)])
    result = run_backtest(
        _make_daily(peak),
        spec=BacktestSpec(ma_fast=5, ma_slow=20, strategy_version="ma-cross-v1"),
        strategy=builtin_strategy(),
    )
    assert result.strategy_version == "ma-cross-v1"
    assert result.parameters == {"ma_fast": 5, "ma_slow": 20}


def test_template_is_valid_user_strategy(isolated) -> None:
    strategy = save_user_strategy(STRATEGY_TEMPLATE)
    assert strategy.source == "user"
    assert strategy.name == "ma_cross_rsi"
