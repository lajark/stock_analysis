"""Small, deterministic, long-only daily backtest and parameter optimizer.

The module is a research tool only. Signals are formed from the close of T and
executed at the open of T+1; it never places orders or calls a broker API.
"""

from __future__ import annotations

import hashlib
import itertools
import time
import tracemalloc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.data.adjustments import ADJUSTMENT_APPLICATION_VERSION


class BacktestError(ValueError):
    """Raised when the input or backtest contract is invalid."""


def _time_budget_exceeded(started_at: float, budget_s: float | None) -> bool:
    """True when a configured wall-clock budget has been used up."""
    return budget_s is not None and (time.perf_counter() - started_at) > budget_s


def _memory_peak_mb() -> float | None:
    """Peak Python-heap allocation in MiB while tracemalloc is active, else None."""
    if tracemalloc.is_tracing():
        return tracemalloc.get_traced_memory()[1] / (1024 * 1024)
    return None


@dataclass(frozen=True)
class CostModel:
    """Versioned A-share-style transaction cost model."""

    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    lot_size: int = 100

    def __post_init__(self) -> None:
        for name in (
            "commission_rate",
            "minimum_commission",
            "stamp_duty_rate",
            "transfer_fee_rate",
            "slippage_bps",
        ):
            if float(getattr(self, name)) < 0:
                raise BacktestError(f"{name} 不得为负数")
        if int(self.lot_size) <= 0:
            raise BacktestError("lot_size 必须为正整数")

    def buy_fees(self, notional: float) -> float:
        commission = max(self.minimum_commission, notional * self.commission_rate)
        return commission + notional * self.transfer_fee_rate

    def sell_fees(self, notional: float) -> float:
        commission = max(self.minimum_commission, notional * self.commission_rate)
        return commission + notional * (self.transfer_fee_rate + self.stamp_duty_rate)

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps / 10_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "commission_rate": self.commission_rate,
            "minimum_commission": self.minimum_commission,
            "stamp_duty_rate": self.stamp_duty_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
            "lot_size": self.lot_size,
        }


@dataclass(frozen=True)
class BacktestSpec:
    """Serializable specification for the first supported strategy."""

    strategy: str = "ma_cross"
    ma_fast: int = 20
    ma_slow: int = 60
    initial_cash: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    strategy_version: str = "ma-cross-v1"
    adjustment: str = "none"

    def __post_init__(self) -> None:
        if self.strategy != "ma_cross":
            raise BacktestError(f"暂不支持策略：{self.strategy}")
        if int(self.ma_fast) <= 0 or int(self.ma_slow) <= 0:
            raise BacktestError("均线参数必须为正整数")
        if int(self.ma_fast) >= int(self.ma_slow):
            raise BacktestError("ma_fast 必须小于 ma_slow")
        if float(self.initial_cash) <= 0:
            raise BacktestError("initial_cash 必须为正数")
        if self.adjustment not in {"none", "qfq", "hfq"}:
            raise BacktestError("adjustment 必须是 none、qfq 或 hfq")

    @property
    def parameters(self) -> dict[str, int]:
        return {"ma_fast": int(self.ma_fast), "ma_slow": int(self.ma_slow)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "parameters": self.parameters,
            "initial_cash": self.initial_cash,
            "costs": self.costs.to_dict(),
            "strategy_version": self.strategy_version,
            "adjustment": self.adjustment,
        }


@dataclass(frozen=True)
class TradeRecord:
    """One completed buy/sell round trip."""

    entry_date: str
    exit_date: str
    shares: int
    entry_price: float
    exit_price: float
    entry_fees: float
    exit_fees: float
    gross_return: float
    net_return: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "shares": self.shares,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_fees": self.entry_fees,
            "exit_fees": self.exit_fees,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
        }


@dataclass(frozen=True)
class BacktestResult:
    """Backtest metrics plus the deterministic audit metadata."""

    strategy_version: str
    parameters: dict[str, int]
    costs: dict[str, Any]
    data_hash: str
    initial_cash: float
    final_equity: float
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    benchmark_return: float
    trade_count: int
    win_rate: float
    turnover: float
    exposure: float
    adjustment: str
    open_shares: int
    equity_curve: pd.DataFrame
    trades: tuple[TradeRecord, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self, *, include_curve: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy_version": self.strategy_version,
            "parameters": dict(self.parameters),
            "costs": dict(self.costs),
            "data_hash": self.data_hash,
            "initial_cash": self.initial_cash,
            "final_equity": self.final_equity,
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "benchmark_return": self.benchmark_return,
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "turnover": self.turnover,
            "exposure": self.exposure,
            "open_shares": self.open_shares,
            "adjustment": self.adjustment,
            "adjustment_application_version": ADJUSTMENT_APPLICATION_VERSION,
            "trades": [trade.to_dict() for trade in self.trades],
            "warnings": list(self.warnings),
        }
        if include_curve:
            curve = self.equity_curve.copy()
            curve["trade_date"] = curve["trade_date"].map(
                lambda value: value.strftime("%Y-%m-%d")
            )
            payload["equity_curve"] = curve.to_dict(orient="records")
        return payload


@dataclass(frozen=True)
class OptimizationResult:
    """Chronological train/validation/test optimization result."""

    objective: str
    selected_parameters: dict[str, int]
    train: BacktestResult
    validation: BacktestResult
    test: BacktestResult
    candidates: tuple[dict[str, Any], ...]
    split_dates: dict[str, str]
    sensitivity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "selected_parameters": dict(self.selected_parameters),
            "train": self.train.to_dict(include_curve=False),
            "validation": self.validation.to_dict(include_curve=False),
            "test": self.test.to_dict(include_curve=False),
            "candidates": [dict(candidate) for candidate in self.candidates],
            "split_dates": dict(self.split_dates),
            "sensitivity": dict(self.sensitivity),
        }


@dataclass(frozen=True)
class RollingOptimizationResult:
    """Walk-forward optimization summary without hiding failed windows."""

    objective: str
    selected_parameters: dict[str, int] | None
    windows: tuple[dict[str, Any], ...]
    parameter_stability: dict[str, Any]
    aggregate: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "selected_parameters": (
                dict(self.selected_parameters)
                if self.selected_parameters is not None
                else None
            ),
            "windows": [dict(window) for window in self.windows],
            "parameter_stability": dict(self.parameter_stability),
            "aggregate": dict(self.aggregate),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MultiOptimizationResult:
    """Equal-weight aggregation of rolling studies across symbols."""

    objective: str
    symbols: tuple[str, ...]
    selected_parameters: dict[str, int] | None
    per_symbol: dict[str, dict[str, Any]]
    groups: dict[str, dict[str, Any]]
    parameter_stability: dict[str, Any]
    aggregate: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "symbols": list(self.symbols),
            "selected_parameters": (
                dict(self.selected_parameters)
                if self.selected_parameters is not None
                else None
            ),
            "per_symbol": {
                symbol: dict(summary)
                for symbol, summary in self.per_symbol.items()
            },
            "groups": {group: dict(summary) for group, summary in self.groups.items()},
            "parameter_stability": dict(self.parameter_stability),
            "aggregate": dict(self.aggregate),
            "warnings": list(self.warnings),
        }


def _prepare_daily(daily: pd.DataFrame) -> pd.DataFrame:
    required = ("trade_date", "open", "close")
    missing = [column for column in required if column not in daily.columns]
    if missing:
        raise BacktestError(f"日线缺少必要字段：{', '.join(missing)}")
    if daily.empty:
        raise BacktestError("日线数据为空")
    frame = daily.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    if frame["trade_date"].isna().any():
        raise BacktestError("日线包含无效交易日期")
    if frame["trade_date"].duplicated().any():
        raise BacktestError("日线包含重复交易日期")
    for column in ("open", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any() or not np.isfinite(frame[column]).all():
            raise BacktestError(f"{column} 包含空值或非有限数值")
        if (frame[column] <= 0).any():
            raise BacktestError(f"{column} 必须为正数")
    return frame.sort_values("trade_date").reset_index(drop=True)


def _data_hash(frame: pd.DataFrame) -> str:
    columns = [
        column
        for column in ("trade_date", "open", "high", "low", "close", "volume")
        if column in frame
    ]
    values = pd.util.hash_pandas_object(frame[columns], index=False).values.tobytes()
    return hashlib.sha256(values).hexdigest()


def _signal_series(frame: pd.DataFrame, spec: BacktestSpec) -> pd.Series:
    fast = frame["close"].rolling(spec.ma_fast, min_periods=spec.ma_fast).mean()
    slow = frame["close"].rolling(spec.ma_slow, min_periods=spec.ma_slow).mean()
    return ((fast > slow) & fast.notna() & slow.notna()).astype(int)


def _annualized_return(total_return: float, dates: pd.Series) -> float:
    days = max(int((dates.iloc[-1] - dates.iloc[0]).days), 1)
    if total_return <= -1:
        return -1.0
    return (1.0 + total_return) ** (365.0 / days) - 1.0


def _sharpe(equity: pd.Series) -> float:
    returns = equity.pct_change().dropna()
    if returns.empty or float(returns.std(ddof=0)) == 0:
        return 0.0
    return float(np.sqrt(252) * returns.mean() / returns.std(ddof=0))


def _sortino(equity: pd.Series) -> float:
    returns = equity.pct_change().dropna()
    downside = returns[returns < 0]
    if returns.empty or downside.empty or float(downside.std(ddof=0)) == 0:
        return 0.0
    return float(np.sqrt(252) * returns.mean() / downside.std(ddof=0))


def _is_suspended(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "停牌"}


def _can_execute(row: pd.Series, *, side: str) -> bool:
    if _is_suspended(row.get("is_suspended")):
        return False
    raw_price = float(row["open"])
    limit_column = "up_limit" if side == "buy" else "down_limit"
    limit = pd.to_numeric(pd.Series([row.get(limit_column)]), errors="coerce").iloc[0]
    if pd.notna(limit):
        if side == "buy" and raw_price >= float(limit):
            return False
        if side == "sell" and raw_price <= float(limit):
            return False
    return True


def run_backtest(
    daily: pd.DataFrame,
    *,
    spec: BacktestSpec | None = None,
) -> BacktestResult:
    """Run a long-only moving-average crossover with T+1 open execution."""
    spec = spec or BacktestSpec()
    frame = _prepare_daily(daily)
    signals = _signal_series(frame, spec)
    costs = spec.costs
    cash = float(spec.initial_cash)
    shares = 0
    entry_date = ""
    entry_price = 0.0
    entry_fees = 0.0
    entry_notional = 0.0
    turnover_notional = 0.0
    trades: list[TradeRecord] = []
    curve_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for column, description in (
        ("is_suspended", "停牌"),
        ("up_limit", "涨停价"),
        ("down_limit", "跌停价"),
    ):
        if column not in frame.columns:
            warnings.append(f"未提供{description}字段，按可交易处理")

    def execute_buy(date: pd.Timestamp, raw_price: float) -> None:
        nonlocal cash, shares, entry_date, entry_price, entry_fees, entry_notional
        nonlocal turnover_notional
        price = raw_price * (1.0 + costs.slippage_rate)
        candidate = int(cash / price) // costs.lot_size * costs.lot_size
        while candidate > 0:
            notional = candidate * price
            fees = costs.buy_fees(notional)
            if notional + fees <= cash:
                break
            candidate -= costs.lot_size
        if candidate <= 0:
            return
        notional = candidate * price
        fees = costs.buy_fees(notional)
        cash -= notional + fees
        shares = candidate
        entry_date = date.strftime("%Y-%m-%d")
        entry_price = price
        entry_fees = fees
        entry_notional = notional
        turnover_notional += notional

    def execute_sell(date: pd.Timestamp, raw_price: float) -> None:
        nonlocal cash, shares, entry_date, entry_price, entry_fees, entry_notional
        nonlocal turnover_notional
        if shares <= 0:
            return
        price = raw_price * (1.0 - costs.slippage_rate)
        notional = shares * price
        fees = costs.sell_fees(notional)
        proceeds = notional - fees
        cash += proceeds
        turnover_notional += notional
        total_entry = entry_notional + entry_fees
        trades.append(
            TradeRecord(
                entry_date=entry_date,
                exit_date=date.strftime("%Y-%m-%d"),
                shares=shares,
                entry_price=entry_price,
                exit_price=price,
                entry_fees=entry_fees,
                exit_fees=fees,
                gross_return=notional / entry_notional - 1.0,
                net_return=proceeds / total_entry - 1.0,
            )
        )
        shares = 0
        entry_date = ""
        entry_price = 0.0
        entry_fees = 0.0
        entry_notional = 0.0

    for index, row in frame.iterrows():
        if index > 0:
            target = int(signals.iloc[index - 1])
            if target == 1 and shares == 0 and _can_execute(row, side="buy"):
                execute_buy(row["trade_date"], float(row["open"]))
            elif target == 0 and shares > 0 and _can_execute(row, side="sell"):
                execute_sell(row["trade_date"], float(row["open"]))
        equity = cash + shares * float(row["close"])
        curve_rows.append(
            {
                "trade_date": row["trade_date"],
                "equity": equity,
                "cash": cash,
                "shares": shares,
                "close": float(row["close"]),
            }
        )

    equity_curve = pd.DataFrame(curve_rows)
    final_equity = float(equity_curve["equity"].iloc[-1])
    total_return = final_equity / spec.initial_cash - 1.0
    wins = sum(1 for trade in trades if trade.net_return > 0)
    if not trades:
        warnings.append("样本期内没有完成交易，收益指标不代表策略有效性")
    return BacktestResult(
        strategy_version=spec.strategy_version,
        parameters=spec.parameters,
        costs=spec.costs.to_dict(),
        data_hash=_data_hash(frame),
        initial_cash=spec.initial_cash,
        final_equity=final_equity,
        total_return=total_return,
        annualized_return=_annualized_return(total_return, frame["trade_date"]),
        max_drawdown=float((equity_curve["equity"] / equity_curve["equity"].cummax() - 1.0).min()),
        sharpe=_sharpe(equity_curve["equity"]),
        sortino=_sortino(equity_curve["equity"]),
        benchmark_return=float(frame["close"].iloc[-1] / frame["close"].iloc[0] - 1.0),
        trade_count=len(trades),
        win_rate=wins / len(trades) if trades else 0.0,
        turnover=turnover_notional / spec.initial_cash,
        exposure=float((equity_curve["shares"] > 0).mean()),
        adjustment=spec.adjustment,
        open_shares=shares,
        equity_curve=equity_curve,
        trades=tuple(trades),
        warnings=tuple(warnings),
    )


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _robust_score_components(result: BacktestResult) -> dict[str, float]:
    """Return the bounded, versioned components used by ``robust``."""
    excess_return = result.total_return - result.benchmark_return
    return {
        "excess_return": _clip(excess_return, -1.0, 1.0),
        "annualized_return": _clip(result.annualized_return, -1.0, 1.0),
        "drawdown_penalty": _clip(abs(result.max_drawdown), 0.0, 1.0),
        "turnover_penalty": _clip(result.turnover / 5.0, 0.0, 1.0),
        "trade_evidence": min(result.trade_count, 10) / 10.0,
    }


def _robust_score(result: BacktestResult) -> float:
    """Calculate the transparent ``robust-v1`` train-selection score.

    Return and excess return are bounded to prevent one extreme sample from
    dominating. Drawdown and turnover are penalties; completed trades provide
    only a small evidence bonus and remain subject to ``min_trades``.
    """
    components = _robust_score_components(result)
    return (
        0.35 * components["excess_return"]
        + 0.25 * components["annualized_return"]
        - 0.25 * components["drawdown_penalty"]
        - 0.10 * components["turnover_penalty"]
        + 0.05 * components["trade_evidence"]
    )


def _objective_value(result: BacktestResult, objective: str) -> float:
    if objective == "sharpe":
        return result.sharpe
    if objective == "total_return":
        return result.total_return
    if objective == "calmar":
        return (
            result.annualized_return / abs(result.max_drawdown)
            if result.max_drawdown < 0
            else 0.0
        )
    if objective == "robust":
        return _robust_score(result)
    raise BacktestError(f"不支持的优化目标：{objective}")


def _parameter_sensitivity(candidates: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarize eligible objective values by each optimized parameter."""
    summary: dict[str, dict[str, Any]] = {}
    for parameter_name in ("ma_fast", "ma_slow"):
        buckets: dict[str, list[float]] = {}
        for candidate in candidates:
            if not candidate.get("eligible", False):
                continue
            parameters = candidate.get("parameters", {})
            if parameter_name not in parameters:
                continue
            key = str(int(parameters[parameter_name]))
            buckets.setdefault(key, []).append(float(candidate["objective_value"]))
        summary[parameter_name] = {
            key: {
                "eligible_count": len(values),
                "objective_min": min(values),
                "objective_median": float(np.median(values)),
                "objective_max": max(values),
            }
            for key, values in sorted(buckets.items(), key=lambda item: int(item[0]))
        }
    return summary


def optimize_ma_cross(
    daily: pd.DataFrame,
    parameter_grid: Mapping[str, Sequence[int]],
    *,
    objective: str = "sharpe",
    train_ratio: float = 0.6,
    validation_ratio: float = 0.2,
    initial_cash: float = 100_000.0,
    costs: CostModel | None = None,
    min_trades: int = 0,
    adjustment: str = "none",
    max_trials: int | None = None,
    time_budget_s: float | None = None,
    memory_budget_mb: int | None = None,
) -> OptimizationResult:
    """Select parameters on train only, then report validation and test results.

    ``time_budget_s`` and ``memory_budget_mb`` are run-time budgets checked
    between trials: when exceeded the best eligible candidate found so far is
    kept and the run is marked ``*_budget_status = "exceeded"``. Memory is
    measured by tracemalloc (Python heap only) and only starts when a budget is
    configured, so the default run is untouched.
    """
    optimization_started = time.perf_counter()
    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1:
        raise BacktestError("train_ratio 和 validation_ratio 必须在 (0, 1) 内")
    if train_ratio + validation_ratio >= 1:
        raise BacktestError("训练集和验证集必须为测试集保留空间")
    if objective not in {"sharpe", "total_return", "calmar", "robust"}:
        raise BacktestError(f"不支持的优化目标：{objective}")
    if int(min_trades) != min_trades or min_trades < 0:
        raise BacktestError("min_trades 必须为非负整数")
    if max_trials is not None and (
        int(max_trials) != max_trials or int(max_trials) <= 0
    ):
        raise BacktestError("max_trials 必须为正整数")
    if time_budget_s is not None and time_budget_s <= 0:
        raise BacktestError("time_budget_s 必须为正数")
    if memory_budget_mb is not None and memory_budget_mb <= 0:
        raise BacktestError("memory_budget_mb 必须为正数")
    if set(parameter_grid) != {"ma_fast", "ma_slow"}:
        raise BacktestError("当前优化器只接受 ma_fast 和 ma_slow")
    frame = _prepare_daily(daily)
    train_end = int(len(frame) * train_ratio)
    validation_end = int(len(frame) * (train_ratio + validation_ratio))
    if min(train_end, validation_end - train_end, len(frame) - validation_end) < 2:
        raise BacktestError("样本太少，无法划分训练/验证/测试区间")
    combinations = [
        (fast, slow)
        for fast, slow in itertools.product(
            sorted({int(value) for value in parameter_grid["ma_fast"]}),
            sorted({int(value) for value in parameter_grid["ma_slow"]}),
        )
        if fast > 0 and slow > 0 and fast < slow
    ]
    if not combinations:
        raise BacktestError("参数网格没有有效组合")
    if max_trials is not None and len(combinations) > int(max_trials):
        raise BacktestError(
            f"有效参数组合数 {len(combinations)} 超过 max_trials={int(max_trials)}"
        )
    candidates: list[dict[str, Any]] = []
    candidate_results: list[tuple[float, int, int, BacktestResult]] = []
    time_budget_stopped = False
    memory_budget_stopped = False
    if memory_budget_mb is not None:
        tracemalloc.start(1)
    try:
        for fast, slow in combinations:
            if fast <= 0 or slow <= 0 or fast >= slow:
                continue
            result = run_backtest(
                frame.iloc[:train_end],
                spec=BacktestSpec(
                    ma_fast=fast,
                    ma_slow=slow,
                    initial_cash=initial_cash,
                    costs=costs or CostModel(),
                    adjustment=adjustment,
                ),
            )
            value = _objective_value(result, objective)
            candidates.append(
                {
                    "parameters": {"ma_fast": fast, "ma_slow": slow},
                    "objective_value": value,
                    "objective_version": "robust-v1" if objective == "robust" else None,
                    "score_components": _robust_score_components(result),
                    "total_return": result.total_return,
                    "sharpe": result.sharpe,
                    "trade_count": result.trade_count,
                    "eligible": result.trade_count >= min_trades,
                    "rejection_reason": (
                        None if result.trade_count >= min_trades else "min_trades"
                    ),
                }
            )
            if result.trade_count >= min_trades:
                candidate_results.append((value, fast, slow, result))
            if _time_budget_exceeded(optimization_started, time_budget_s):
                time_budget_stopped = True
                break
            if (
                memory_budget_mb is not None
                and tracemalloc.get_traced_memory()[0]
                > memory_budget_mb * 1024 * 1024
            ):
                memory_budget_stopped = True
                break
    finally:
        memory_peak_mb_observed = _memory_peak_mb()
        if memory_budget_mb is not None:
            tracemalloc.stop()
    if not candidates:
        raise BacktestError("参数网格没有有效组合")
    if (time_budget_stopped or memory_budget_stopped) and not candidate_results:
        raise BacktestError("资源预算已耗尽，未产生合格候选")
    if not candidate_results:
        raise BacktestError("参数网格没有满足最小交易次数的组合")
    candidate_results.sort(key=lambda row: (-row[0], row[1], row[2]))
    rank_by_parameters = {
        (row[1], row[2]): rank for rank, row in enumerate(candidate_results, start=1)
    }
    for candidate in candidates:
        parameters = candidate["parameters"]
        candidate["rank"] = rank_by_parameters.get(
            (parameters["ma_fast"], parameters["ma_slow"])
        )
    _, selected_fast, selected_slow, train_result = candidate_results[0]
    objective_values = [row[0] for row in candidate_results]
    top_value = objective_values[0]
    runner_up = objective_values[1] if len(objective_values) > 1 else None
    tolerance = max(abs(top_value) * 0.05, 0.01)
    sensitivity = {
        "candidate_count": len(candidates),
        "eligible_count": len(candidate_results),
        "selected_objective": top_value,
        "runner_up_objective": runner_up,
        "top_margin": top_value - runner_up if runner_up is not None else None,
        "objective_median": float(np.median(objective_values)),
        "near_optimal_tolerance": tolerance,
        "near_optimal_count": sum(
            1 for value in objective_values if value >= top_value - tolerance
        ),
        "trial_count": len(candidates),
        "trial_budget": int(max_trials) if max_trials is not None else None,
        "trial_budget_status": "within_limit" if max_trials is not None else "not_configured",
        "time_budget_s": int(time_budget_s) if time_budget_s is not None else None,
        "time_budget_status": (
            "exceeded"
            if time_budget_stopped
            else "within_limit"
            if time_budget_s is not None
            else "not_configured"
        ),
        "memory_budget_mb": (
            int(memory_budget_mb) if memory_budget_mb is not None else None
        ),
        "memory_peak_mb": (
            round(memory_peak_mb_observed, 1)
            if memory_peak_mb_observed is not None
            else None
        ),
        "memory_budget_status": (
            "exceeded"
            if memory_budget_stopped
            else "within_limit"
            if memory_budget_mb is not None
            else "not_configured"
        ),
        "parameter_sensitivity": _parameter_sensitivity(candidates),
        "ranking_method_version": "objective-desc-fast-slow-v1",
        "method_version": "robust-v1" if objective == "robust" else "ranking-v1",
    }
    selected_spec = BacktestSpec(
        ma_fast=selected_fast,
        ma_slow=selected_slow,
        initial_cash=initial_cash,
        costs=costs or CostModel(),
        adjustment=adjustment,
    )
    validation_result = run_backtest(frame.iloc[train_end:validation_end], spec=selected_spec)
    test_result = run_backtest(frame.iloc[validation_end:], spec=selected_spec)
    split_dates = {
        "train_start": frame["trade_date"].iloc[0].strftime("%Y-%m-%d"),
        "train_end": frame["trade_date"].iloc[train_end - 1].strftime("%Y-%m-%d"),
        "validation_start": frame["trade_date"].iloc[train_end].strftime("%Y-%m-%d"),
        "validation_end": frame["trade_date"].iloc[validation_end - 1].strftime("%Y-%m-%d"),
        "test_start": frame["trade_date"].iloc[validation_end].strftime("%Y-%m-%d"),
        "test_end": frame["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
    }
    sensitivity["elapsed_ms"] = int(
        round((time.perf_counter() - optimization_started) * 1000)
    )
    return OptimizationResult(
        objective=objective,
        selected_parameters={"ma_fast": selected_fast, "ma_slow": selected_slow},
        train=train_result,
        validation=validation_result,
        test=test_result,
        candidates=tuple(candidates),
        split_dates=split_dates,
        sensitivity=sensitivity,
    )


def optimize_ma_cross_rolling(
    daily: pd.DataFrame,
    parameter_grid: Mapping[str, Sequence[int]],
    *,
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int | None = None,
    objective: str = "sharpe",
    initial_cash: float = 100_000.0,
    costs: CostModel | None = None,
    min_trades: int = 1,
    adjustment: str = "none",
    max_trials: int | None = None,
    time_budget_s: float | None = None,
    memory_budget_mb: int | None = None,
) -> RollingOptimizationResult:
    """Run chronological walk-forward optimization and report stability.

    Budgets are enforced at window boundaries only (top-level total run-time
    budget and optional tracemalloc memory budget); completed windows always
    stay in the output when an early stop happens.

    Parameters are selected from each training slice only. Validation and test
    slices are used for measurement, never for selecting the next parameter
    set. Failed windows remain in the output so a sparse or unstable sample
    cannot be mistaken for a complete optimization.
    """
    rolling_started = time.perf_counter()
    if objective not in {"sharpe", "total_return", "calmar", "robust"}:
        raise BacktestError(f"不支持的优化目标：{objective}")
    sizes = {
        "train_size": train_size,
        "validation_size": validation_size,
        "test_size": test_size,
    }
    for name, value in sizes.items():
        if int(value) != value or int(value) <= 0:
            raise BacktestError(f"{name} 必须为正整数")
    if min_trades < 0 or int(min_trades) != min_trades:
        raise BacktestError("min_trades 必须为非负整数")
    if max_trials is not None and (
        int(max_trials) != max_trials or int(max_trials) <= 0
    ):
        raise BacktestError("max_trials 必须为正整数")
    if time_budget_s is not None and time_budget_s <= 0:
        raise BacktestError("time_budget_s 必须为正数")
    if memory_budget_mb is not None and memory_budget_mb <= 0:
        raise BacktestError("memory_budget_mb 必须为正数")
    step = test_size if step_size is None else step_size
    if int(step) != step or int(step) <= 0:
        raise BacktestError("step_size 必须为正整数")
    if set(parameter_grid) != {"ma_fast", "ma_slow"}:
        raise BacktestError("当前优化器只接受 ma_fast 和 ma_slow")

    frame = _prepare_daily(daily)
    total_size = train_size + validation_size + test_size
    if total_size > len(frame):
        raise BacktestError("滚动窗口总长度超过日线样本长度")

    windows: list[dict[str, Any]] = []
    successful: list[dict[str, Any]] = []
    warnings: list[str] = []
    max_start = len(frame) - total_size
    time_budget_stopped = False
    memory_budget_stopped = False
    if memory_budget_mb is not None:
        tracemalloc.start(1)
    for window_index, start in enumerate(range(0, max_start + 1, int(step))):
        if _time_budget_exceeded(rolling_started, time_budget_s):
            time_budget_stopped = True
            break
        if memory_budget_mb is not None and (
            tracemalloc.get_traced_memory()[0] > memory_budget_mb * 1024 * 1024
        ):
            memory_budget_stopped = True
            break
        window_frame = frame.iloc[start : start + total_size].reset_index(drop=True)
        window_started = time.perf_counter()
        try:
            result = optimize_ma_cross(
                window_frame,
                parameter_grid,
                objective=objective,
                train_ratio=train_size / total_size,
                validation_ratio=validation_size / total_size,
                initial_cash=initial_cash,
                costs=costs,
                min_trades=min_trades,
                adjustment=adjustment,
                max_trials=max_trials,
            )
        except BacktestError as exc:
            message = str(exc)
            warnings.append(f"窗口 {window_index} 失败：{message}")
            windows.append(
                {
                    "window_index": window_index,
                    "status": "failed",
                    "start": window_frame["trade_date"].iloc[0].strftime("%Y-%m-%d"),
                    "end": window_frame["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
                    "error": message,
                    "trial_budget": int(max_trials) if max_trials is not None else None,
                    "elapsed_ms": int(
                        round((time.perf_counter() - window_started) * 1000)
                    ),
                }
            )
            continue

        window = {
            "window_index": window_index,
            "status": "ok",
            "start": window_frame["trade_date"].iloc[0].strftime("%Y-%m-%d"),
            "end": window_frame["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
            "split_dates": dict(result.split_dates),
            "selected_parameters": dict(result.selected_parameters),
            "candidate_count": len(result.candidates),
            "trial_budget": int(max_trials) if max_trials is not None else None,
            "sensitivity": dict(result.sensitivity),
            "elapsed_ms": int(round((time.perf_counter() - window_started) * 1000)),
            "train_objective": _objective_value(result.train, objective),
            "validation_objective": _objective_value(result.validation, objective),
            "test_objective": _objective_value(result.test, objective),
            "train_total_return": result.train.total_return,
            "validation_total_return": result.validation.total_return,
            "test_total_return": result.test.total_return,
            "train_trade_count": result.train.trade_count,
            "validation_trade_count": result.validation.trade_count,
            "test_trade_count": result.test.trade_count,
            "warnings": list(
                result.train.warnings
                + result.validation.warnings
                + result.test.warnings
            ),
        }
        if (
            result.validation.trade_count < min_trades
            or result.test.trade_count < min_trades
        ):
            window["status"] = "degraded"
            warning = f"窗口 {window_index} 的验证或测试交易次数低于 {min_trades}"
            window["warnings"].append(warning)
            warnings.append(warning)
        windows.append(window)
        successful.append(window)

    memory_peak_mb_observed = _memory_peak_mb()
    if memory_budget_mb is not None:
        tracemalloc.stop()
    selection_counts: dict[str, int] = {}
    parameter_values: dict[str, dict[str, int]] = {}
    for window in successful:
        parameters = window["selected_parameters"]
        key = f"{parameters['ma_fast']},{parameters['ma_slow']}"
        selection_counts[key] = selection_counts.get(key, 0) + 1
        parameter_values[key] = dict(parameters)
    successful_count = len(successful)
    if selection_counts:
        dominant_key = max(
            selection_counts,
            key=lambda key: (
                selection_counts[key],
                -parameter_values[key]["ma_fast"],
                -parameter_values[key]["ma_slow"],
            ),
        )
        dominant_parameters = parameter_values[dominant_key]
        dominant_frequency = selection_counts[dominant_key] / successful_count
    else:
        dominant_key = None
        dominant_parameters = None
        dominant_frequency = 0.0
    stable = successful_count >= 2 and dominant_frequency >= 0.6
    if successful_count == 0:
        warnings.append("没有成功完成的滚动窗口，无法形成优化结论")
    elif not stable:
        warnings.append("参数在滚动窗口中不稳定，不输出可直接采用的参数")

    def median(field: str) -> float | None:
        values = [float(window[field]) for window in successful]
        return float(np.median(values)) if values else None

    aggregate = {
        "window_count": len(windows),
        "successful_windows": successful_count,
        "failed_windows": len(windows) - successful_count,
        "min_trades": int(min_trades),
        "validation_objective_median": median("validation_objective"),
        "test_objective_median": median("test_objective"),
        "validation_total_return_median": median("validation_total_return"),
        "test_total_return_median": median("test_total_return"),
        "validation_trade_count_median": median("validation_trade_count"),
        "test_trade_count_median": median("test_trade_count"),
        "trial_count": sum(int(window.get("candidate_count", 0)) for window in successful),
        "trial_budget": int(max_trials) if max_trials is not None else None,
        "time_budget_s": int(time_budget_s) if time_budget_s is not None else None,
        "time_budget_status": (
            "exceeded"
            if time_budget_stopped
            else "within_limit"
            if time_budget_s is not None
            else "not_configured"
        ),
        "memory_budget_mb": (
            int(memory_budget_mb) if memory_budget_mb is not None else None
        ),
        "memory_peak_mb": (
            round(memory_peak_mb_observed, 1)
            if memory_peak_mb_observed is not None
            else None
        ),
        "memory_budget_status": (
            "exceeded"
            if memory_budget_stopped
            else "within_limit"
            if memory_budget_mb is not None
            else "not_configured"
        ),
        "elapsed_ms": int(round((time.perf_counter() - rolling_started) * 1000)),
    }
    return RollingOptimizationResult(
        objective=objective,
        selected_parameters=(
            dict(dominant_parameters)
            if stable and dominant_parameters is not None
            else None
        ),
        windows=tuple(windows),
        parameter_stability={
            "stable": stable,
            "threshold": 0.6,
            "dominant_key": dominant_key,
            "dominant_frequency": dominant_frequency,
            "selection_counts": selection_counts,
            "successful_windows": successful_count,
        },
        aggregate=aggregate,
        warnings=tuple(warnings),
    )


def optimize_ma_cross_multi(
    daily_by_symbol: Mapping[str, pd.DataFrame],
    parameter_grid: Mapping[str, Sequence[int]],
    *,
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int | None = None,
    objective: str = "robust",
    initial_cash: float = 100_000.0,
    costs: CostModel | None = None,
    min_trades: int = 1,
    market_state_by_symbol: Mapping[str, str] | None = None,
    min_successful_symbols: int = 2,
    adjustment: str = "none",
    max_trials: int | None = None,
    time_budget_s: float | None = None,
    memory_budget_mb: int | None = None,
) -> MultiOptimizationResult:
    """Aggregate rolling studies with equal weight per successful symbol.

    Budgets are enforced between symbols (total run time / optional tracemalloc
    memory); already-completed symbol studies always stay in the output.

    A symbol contributes its own parameter frequency distribution, rather than
    one vote per raw window. This prevents a symbol with more available dates
    from dominating the cross-symbol conclusion. ``market_state_by_symbol``
    only labels groups for audit and comparison; it does not alter selection.
    """
    multi_started = time.perf_counter()
    if not daily_by_symbol:
        raise BacktestError("至少需要一个标的的日线数据")
    if int(min_successful_symbols) != min_successful_symbols or min_successful_symbols <= 0:
        raise BacktestError("min_successful_symbols 必须为正整数")
    if max_trials is not None and (
        int(max_trials) != max_trials or int(max_trials) <= 0
    ):
        raise BacktestError("max_trials 必须为正整数")
    if time_budget_s is not None and time_budget_s <= 0:
        raise BacktestError("time_budget_s 必须为正数")
    if memory_budget_mb is not None and memory_budget_mb <= 0:
        raise BacktestError("memory_budget_mb 必须为正数")
    normalized_data = {str(symbol): frame for symbol, frame in daily_by_symbol.items()}
    symbols = tuple(sorted(normalized_data))
    if any(not symbol for symbol in symbols):
        raise BacktestError("标的代码不能为空")
    states = market_state_by_symbol or {}
    studies: dict[str, RollingOptimizationResult] = {}
    per_symbol: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    time_budget_stopped = False
    memory_budget_stopped = False
    if memory_budget_mb is not None:
        tracemalloc.start(1)
    for symbol in symbols:
        if _time_budget_exceeded(multi_started, time_budget_s):
            time_budget_stopped = True
            break
        if memory_budget_mb is not None and (
            tracemalloc.get_traced_memory()[0] > memory_budget_mb * 1024 * 1024
        ):
            memory_budget_stopped = True
            break
        symbol_started = time.perf_counter()
        try:
            study = optimize_ma_cross_rolling(
                normalized_data[symbol],
                parameter_grid,
                train_size=train_size,
                validation_size=validation_size,
                test_size=test_size,
                step_size=step_size,
                objective=objective,
                initial_cash=initial_cash,
                costs=costs,
                min_trades=min_trades,
                adjustment=adjustment,
                max_trials=max_trials,
            )
        except BacktestError as exc:
            message = f"{symbol} 优化失败：{exc}"
            warnings.append(message)
            per_symbol[symbol] = {
                "status": "failed",
                "group": str(states.get(symbol, "unknown")),
                "error": str(exc),
                "elapsed_ms": int(
                    round((time.perf_counter() - symbol_started) * 1000)
                ),
            }
            continue
        studies[symbol] = study
        summary = study.to_dict()
        summary["status"] = "ok" if study.aggregate["successful_windows"] else "failed"
        summary["group"] = str(states.get(symbol, "unknown"))
        per_symbol[symbol] = summary
        warnings.extend(f"{symbol}: {warning}" for warning in study.warnings)

    memory_peak_mb_observed = _memory_peak_mb()
    if memory_budget_mb is not None:
        tracemalloc.stop()

    def selection_summary(selected_symbols: Sequence[str]) -> dict[str, Any]:
        eligible_symbols = [
            symbol
            for symbol in selected_symbols
            if symbol in studies
            and int(studies[symbol].aggregate["successful_windows"]) > 0
        ]
        raw_counts: dict[str, int] = {}
        weighted_frequency: dict[str, float] = {}
        parameter_values: dict[str, dict[str, int]] = {}
        for symbol in eligible_symbols:
            windows = [
                window
                for window in studies[symbol].windows
                if window["status"] in {"ok", "degraded"}
            ]
            counts: dict[str, int] = {}
            for window in windows:
                parameters = window["selected_parameters"]
                key = f"{parameters['ma_fast']},{parameters['ma_slow']}"
                counts[key] = counts.get(key, 0) + 1
                raw_counts[key] = raw_counts.get(key, 0) + 1
                parameter_values[key] = dict(parameters)
            for key, count in counts.items():
                weighted_frequency[key] = weighted_frequency.get(key, 0.0) + (
                    count / len(windows) / len(eligible_symbols)
                )
        if weighted_frequency:
            dominant_key = max(
                weighted_frequency,
                key=lambda key: (
                    weighted_frequency[key],
                    -parameter_values[key]["ma_fast"],
                    -parameter_values[key]["ma_slow"],
                ),
            )
            dominant_parameters = parameter_values[dominant_key]
            dominant_frequency = weighted_frequency[dominant_key]
        else:
            dominant_key = None
            dominant_parameters = None
            dominant_frequency = 0.0
        stable = (
            len(eligible_symbols) >= min_successful_symbols
            and dominant_frequency >= 0.6
        )
        return {
            "stable": stable,
            "threshold": 0.6,
            "dominant_key": dominant_key,
            "dominant_frequency": dominant_frequency,
            "selection_counts": raw_counts,
            "weighted_selection_frequency": weighted_frequency,
            "successful_symbols": len(eligible_symbols),
            "selected_parameters": (
                dict(dominant_parameters) if dominant_parameters is not None else None
            ),
        }

    global_stability = selection_summary(symbols)
    if not studies:
        warnings.append("没有成功完成的标的优化，无法形成聚合结论")
    elif not global_stability["stable"]:
        warnings.append("跨标的参数不稳定，不输出可直接采用的聚合参数")

    grouped_symbols: dict[str, list[str]] = {}
    for symbol in symbols:
        group = str(states.get(symbol, "unknown"))
        grouped_symbols.setdefault(group, []).append(symbol)
    groups: dict[str, dict[str, Any]] = {}
    for group, group_symbols in sorted(grouped_symbols.items()):
        stability = selection_summary(group_symbols)
        group_values = [
            float(studies[symbol].aggregate["test_objective_median"])
            for symbol in group_symbols
            if symbol in studies
            and studies[symbol].aggregate["test_objective_median"] is not None
        ]
        groups[group] = {
            "symbols": group_symbols,
            "parameter_stability": stability,
            "test_objective_median": (
                float(np.median(group_values)) if group_values else None
            ),
        }
    test_values = [
        float(studies[symbol].aggregate["test_objective_median"])
        for symbol in studies
        if studies[symbol].aggregate["test_objective_median"] is not None
    ]
    aggregate = {
        "symbol_count": len(symbols),
        "successful_symbols": len(studies),
        "failed_symbols": len(symbols) - len(studies),
        "successful_windows": sum(
            int(study.aggregate["successful_windows"]) for study in studies.values()
        ),
        "group_count": len(groups),
        "min_successful_symbols": int(min_successful_symbols),
        "trial_budget": int(max_trials) if max_trials is not None else None,
        "time_budget_s": int(time_budget_s) if time_budget_s is not None else None,
        "time_budget_status": (
            "exceeded"
            if time_budget_stopped
            else "within_limit"
            if time_budget_s is not None
            else "not_configured"
        ),
        "memory_budget_mb": (
            int(memory_budget_mb) if memory_budget_mb is not None else None
        ),
        "memory_peak_mb": (
            round(memory_peak_mb_observed, 1)
            if memory_peak_mb_observed is not None
            else None
        ),
        "memory_budget_status": (
            "exceeded"
            if memory_budget_stopped
            else "within_limit"
            if memory_budget_mb is not None
            else "not_configured"
        ),
        "test_objective_median": float(np.median(test_values)) if test_values else None,
        "trial_count": sum(
            int(study.aggregate.get("trial_count", 0)) for study in studies.values()
        ),
        "elapsed_ms": int(round((time.perf_counter() - multi_started) * 1000)),
    }
    selected_parameters = (
        dict(global_stability["selected_parameters"])
        if global_stability["stable"]
        and global_stability["selected_parameters"] is not None
        else None
    )
    return MultiOptimizationResult(
        objective=objective,
        symbols=symbols,
        selected_parameters=selected_parameters,
        per_symbol=per_symbol,
        groups=groups,
        parameter_stability=global_stability,
        aggregate=aggregate,
        warnings=tuple(warnings),
    )
