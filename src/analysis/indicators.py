"""技术指标计算 — 纯 Python 实现，无外部依赖。

从 Stocks/backend/indicators/ 迁移，简化为函数式接口。
所有指标计算仅依赖 Pandas 和 NumPy。
"""

import numpy as np
import pandas as pd

from src.analysis.parameters import AnalysisParameters


# ------------------------------------------------------------------
# 移动平均线
# ------------------------------------------------------------------
def calc_ma(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """移动平均线 (MA)。"""
    if periods is None:
        periods = [5, 10, 20, 60]
    result = df.copy()
    for p in periods:
        result[f"ma_{p}"] = df["close"].rolling(window=p).mean()
    return result


def calc_ema(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """指数移动平均线 (EMA)。"""
    if periods is None:
        periods = [12, 26]
    result = df.copy()
    for p in periods:
        result[f"ema_{p}"] = df["close"].ewm(span=p).mean()
    return result


# ------------------------------------------------------------------
# 布林带
# ------------------------------------------------------------------
def calc_bollinger(
    df: pd.DataFrame, period: int = 20, std_mult: float = 2.0
) -> pd.DataFrame:
    """布林带 (Bollinger Bands)。"""
    result = df.copy()
    result["bb_middle"] = df["close"].rolling(window=period).mean()
    bb_std = df["close"].rolling(window=period).std()
    result["bb_upper"] = result["bb_middle"] + std_mult * bb_std
    result["bb_lower"] = result["bb_middle"] - std_mult * bb_std
    result["bb_width"] = result["bb_upper"] - result["bb_lower"]
    result["bb_percent"] = (df["close"] - result["bb_lower"]) / result["bb_width"]
    return result


# ------------------------------------------------------------------
# RSI
# ------------------------------------------------------------------
def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """相对强弱指数 (RSI)。"""
    result = df.copy()
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    result["rsi"] = 100.0 - (100.0 / (1.0 + rs))
    return result


# ------------------------------------------------------------------
# MACD
# ------------------------------------------------------------------
def calc_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD 指标。"""
    result = df.copy()
    ema_fast = df["close"].ewm(span=fast).mean()
    ema_slow = df["close"].ewm(span=slow).mean()
    result["macd"] = ema_fast - ema_slow
    result["macd_signal"] = result["macd"].ewm(span=signal).mean()
    result["macd_histogram"] = result["macd"] - result["macd_signal"]
    return result


# ------------------------------------------------------------------
# KDJ
# ------------------------------------------------------------------
def calc_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """KDJ 随机指标。"""
    result = df.copy()
    low_n = df["low"].rolling(window=n).min()
    high_n = df["high"].rolling(window=n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100.0
    result["kdj_k"] = rsv.ewm(com=m1 - 1).mean()
    result["kdj_d"] = result["kdj_k"].ewm(com=m2 - 1).mean()
    result["kdj_j"] = 3.0 * result["kdj_k"] - 2.0 * result["kdj_d"]
    return result


# ------------------------------------------------------------------
# CCI
# ------------------------------------------------------------------
def calc_cci(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """顺势指标 (CCI)。"""
    result = df.copy()
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma = tp.rolling(window=period).mean()
    mad = tp.rolling(window=period).apply(lambda x: np.mean(np.abs(x - x.mean())))
    result["cci"] = (tp - sma) / (0.015 * mad)
    return result


# ------------------------------------------------------------------
# 威廉指标
# ------------------------------------------------------------------
def calc_williams_r(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """威廉指标 (Williams %R)。"""
    result = df.copy()
    high_n = df["high"].rolling(window=period).max()
    low_n = df["low"].rolling(window=period).min()
    result["williams_r"] = (high_n - df["close"]) / (high_n - low_n) * -100.0
    return result


# ------------------------------------------------------------------
# OBV
# ------------------------------------------------------------------
def calc_obv(df: pd.DataFrame) -> pd.DataFrame:
    """能量潮指标 (OBV)。"""
    result = df.copy()
    price_change = df["close"].diff()
    obv = np.zeros(len(df))
    obv[0] = float(df["volume"].iloc[0])
    for i in range(1, len(df)):
        if price_change.iloc[i] > 0:
            obv[i] = obv[i - 1] + float(df["volume"].iloc[i])
        elif price_change.iloc[i] < 0:
            obv[i] = obv[i - 1] - float(df["volume"].iloc[i])
        else:
            obv[i] = obv[i - 1]
    result["obv"] = obv
    result["obv_ma"] = pd.Series(obv).rolling(window=20).mean().values
    return result


# ------------------------------------------------------------------
# 成交量比率
# ------------------------------------------------------------------
def calc_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """成交量比率。"""
    result = df.copy()
    volume_ma = df["volume"].rolling(window=period).mean()
    result["volume_ratio"] = df["volume"] / volume_ma
    return result


# ------------------------------------------------------------------
# 批量计算全部指标
# ------------------------------------------------------------------
def calc_all_indicators(
    df: pd.DataFrame,
    parameters: AnalysisParameters | None = None,
) -> pd.DataFrame:
    """计算全部技术指标，合并到一张 DataFrame。

    ``parameters`` 显式传入时不会读取或修改全局配置，便于回测和参数
    优化复现；缺省值保持与历史调用兼容。
    """
    parameters = parameters or AnalysisParameters()
    df = calc_ma(df, list(parameters.ma_periods))
    df = calc_ema(df, [parameters.macd_fast, parameters.macd_slow])
    df = calc_bollinger(df, parameters.bollinger_period, parameters.bollinger_std)
    df = calc_rsi(df, parameters.rsi_period)
    df = calc_macd(
        df,
        parameters.macd_fast,
        parameters.macd_slow,
        parameters.macd_signal,
    )
    df = calc_kdj(df, parameters.kdj_n, parameters.kdj_m1, parameters.kdj_m2)
    df = calc_cci(df, parameters.bollinger_period)
    df = calc_williams_r(df, parameters.rsi_period)
    df = calc_obv(df)
    df = calc_volume_ratio(df, parameters.bollinger_period)
    return df


# ------------------------------------------------------------------
# 指标摘要
# ------------------------------------------------------------------
def summarize_indicators(df: pd.DataFrame) -> dict:
    """提取最新一行的技术指标摘要。

    供 LLM 消费的结构化摘要，不包含原始数据。
    """
    if df.empty:
        return {}

    latest = df.iloc[-1]
    close = float(latest["close"])

    # 趋势判断
    ma_5 = float(latest.get("ma_5", close))
    ma_20 = float(latest.get("ma_20", close))
    ma_60 = float(latest.get("ma_60", close))
    trend = "上升" if ma_5 > ma_20 > ma_60 else ("下降" if ma_5 < ma_20 < ma_60 else "震荡")

    # MACD 信号
    macd = float(latest.get("macd", 0))
    macd_signal = float(latest.get("macd_signal", 0))
    macd_status = "金叉" if macd > macd_signal else "死叉"

    # RSI 状态
    rsi = float(latest.get("rsi", 50))
    if rsi > 70:
        rsi_status = "超买"
    elif rsi < 30:
        rsi_status = "超卖"
    else:
        rsi_status = "中性"

    # KDJ 状态
    k = float(latest.get("kdj_k", 50))
    d = float(latest.get("kdj_d", 50))
    j = float(latest.get("kdj_j", 50))
    kdj_status = "超买" if k > 80 and d > 80 else ("超卖" if k < 20 and d < 20 else "中性")

    # 布林带位置
    bb_upper = float(latest.get("bb_upper", close * 1.1))
    bb_lower = float(latest.get("bb_lower", close * 0.9))
    if close > bb_upper:
        bb_pos = "上轨上方"
    elif close < bb_lower:
        bb_pos = "下轨下方"
    else:
        bb_pos = "轨道内"

    return {
        "close": round(close, 2),
        "trend": trend,
        "ma_5": round(ma_5, 2),
        "ma_20": round(ma_20, 2),
        "ma_60": round(ma_60, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_signal, 4),
        "macd_status": macd_status,
        "rsi": round(rsi, 1),
        "rsi_status": rsi_status,
        "kdj_k": round(k, 1),
        "kdj_d": round(d, 1),
        "kdj_j": round(j, 1),
        "kdj_status": kdj_status,
        "bollinger_position": bb_pos,
        "volume_ratio": round(float(latest.get("volume_ratio", 1.0)), 2),
        "cci": round(float(latest.get("cci", 0)), 1),
    }
