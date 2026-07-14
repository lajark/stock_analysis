"""数据校验 — 空值、异常值、复权一致性检查。

所有校验规则可配置，在校验失败时记录警告日志而非中断流程。
"""

from typing import Any

import pandas as pd
from loguru import logger


class DataValidator:
    """数据校验器。

    在校验失败时记录警告，返回校验结果字典。
    不中断分析流程，由调用方决定如何处理。
    """

    def __init__(self):
        self._warnings: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 日线行情校验
    # ------------------------------------------------------------------
    def validate_daily(self, df: pd.DataFrame, code: str) -> dict[str, Any]:
        """校验日线行情数据。

        Returns:
            dict with keys: is_valid, warnings, issues
        """
        issues = []

        if df.empty:
            return {"is_valid": False, "warnings": ["日线数据为空"], "issues": ["empty"]}

        required_cols = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            issues.append(f"缺少列: {missing}")

        # 空值检查
        for col in required_cols:
            if col in df.columns:
                null_count = df[col].isna().sum()
                if null_count > 0:
                    issues.append(f"{col} 有 {null_count} 个空值")
                    logger.warning(f"[{code}] 日线 {col} 存在 {null_count} 个空值")

        # 价格合理性
        if "close" in df.columns and "open" in df.columns:
            # 涨跌幅超过 11%（A股正常涨跌停为 10%）
            if len(df) >= 2:
                pct_changes = abs(df["close"].pct_change())
                anomalies = pct_changes[pct_changes > 0.11]
                if len(anomalies) > 0:
                    dates = df.loc[anomalies.index, "trade_date"].astype(str).tolist()
                    issues.append(f"涨跌幅异常 (>11%): {len(anomalies)} 处")
                    logger.warning(f"[{code}] 涨跌幅异常: {dates}")

        # 价格逻辑：high >= max(open, close), low <= min(open, close)
        if all(c in df.columns for c in ["high", "low", "open", "close"]):
            bad_high = df["high"] < df[["open", "close"]].max(axis=1)
            bad_low = df["low"] > df[["open", "close"]].min(axis=1)
            if bad_high.any() or bad_low.any():
                issues.append("OHLC 价格逻辑异常")
                logger.warning(f"[{code}] OHLC 价格逻辑异常")

        # 成交量合理性
        if "volume" in df.columns:
            zero_vol = (df["volume"] == 0).sum()
            if zero_vol > 0:
                issues.append(f"成交量为零: {zero_vol} 天")
                logger.warning(f"[{code}] 成交量为零: {zero_vol} 天")

        is_valid = len(issues) == 0
        return {
            "is_valid": is_valid,
            "warnings": issues,
            "issues": issues,
            "row_count": len(df),
        }

    # ------------------------------------------------------------------
    # 财务数据校验
    # ------------------------------------------------------------------
    def validate_financials(
        self, df: pd.DataFrame, report_type: str, code: str
    ) -> dict[str, Any]:
        """校验财务数据。"""
        issues = []

        if df.empty:
            return {
                "is_valid": False,
                "warnings": [f"{report_type} 数据为空"],
                "issues": ["empty"],
            }

        # 空值率检查
        null_ratio = df.isna().mean()
        high_null_cols = null_ratio[null_ratio > 0.5].index.tolist()
        if high_null_cols:
            issues.append(f"{report_type} 高空值列: {high_null_cols}")
            logger.warning(f"[{code}] {report_type} 高空值率: {high_null_cols}")

        # 报告期连续性
        if "end_date" in df.columns:
            df_sorted = df.sort_values("end_date")
            if len(df_sorted) >= 2:
                # 检查报告期间隔（正常为 3 个月）
                dates = pd.to_datetime(df_sorted["end_date"])
                gaps = dates.diff().dropna()
                large_gaps = gaps[gaps > pd.Timedelta(days=200)]
                if len(large_gaps) > 0:
                    issues.append(f"{report_type} 报告期间隔异常: {len(large_gaps)} 处")
                    logger.warning(f"[{code}] {report_type} 报告期间隔异常")

        # 负值合理性检查（部分指标不应为负）
        never_negative = {
            "income": ["revenue"],
            "balance_sheet": ["total_assets", "shareholders_equity"],
            "fina_indicator": ["roe", "roa", "gross_margin"],
        }
        if report_type in never_negative:
            for col in never_negative[report_type]:
                if col in df.columns:
                    neg = (df[col] < 0).sum()
                    if neg > 0:
                        issues.append(f"{col} 出现负值: {neg} 期")
                        logger.warning(f"[{code}] {report_type}.{col} 负值: {neg} 期")

        is_valid = len(issues) == 0
        return {
            "is_valid": is_valid,
            "warnings": issues,
            "issues": issues,
            "row_count": len(df),
        }

    # ------------------------------------------------------------------
    # 复权一致性校验
    # ------------------------------------------------------------------
    def validate_adj_consistency(
        self, daily: pd.DataFrame, adj_factor: pd.DataFrame, code: str
    ) -> dict[str, Any]:
        """校验复权因子与日线数据的一致性。

        检查复权因子的日期范围是否覆盖日线数据。
        """
        issues = []

        if adj_factor.empty:
            issues.append("复权因子数据为空，无法生成复权行情")
            logger.warning(f"[{code}] 复权因子为空")
            return {"is_valid": False, "warnings": issues, "issues": issues}

        if not daily.empty and not adj_factor.empty:
            daily_min = daily["trade_date"].min()
            daily_max = daily["trade_date"].max()
            adj_min = adj_factor["trade_date"].min()
            adj_max = adj_factor["trade_date"].max()

            if adj_min > daily_min:
                issues.append(
                    f"复权因子起始日期({adj_min.strftime('%Y-%m-%d')})晚于日线起始日期({daily_min.strftime('%Y-%m-%d')})"
                )
            if adj_max < daily_max:
                issues.append(
                    f"复权因子结束日期({adj_max.strftime('%Y-%m-%d')})早于日线结束日期({daily_max.strftime('%Y-%m-%d')})"
                )

        is_valid = len(issues) == 0
        return {"is_valid": is_valid, "warnings": issues, "issues": issues}

    # ------------------------------------------------------------------
    # 综合校验
    # ------------------------------------------------------------------
    def validate_all(
        self,
        code: str,
        daily: pd.DataFrame,
        adj_factor: pd.DataFrame,
        income: pd.DataFrame,
        balance_sheet: pd.DataFrame,
        cashflow: pd.DataFrame,
        fina_indicator: pd.DataFrame,
    ) -> dict[str, Any]:
        """运行所有校验规则。"""
        results = {
            "daily": self.validate_daily(daily, code),
            "adj_consistency": self.validate_adj_consistency(daily, adj_factor, code),
            "income": self.validate_financials(income, "income", code),
            "balance_sheet": self.validate_financials(balance_sheet, "balance_sheet", code),
            "cashflow": self.validate_financials(cashflow, "cashflow", code),
            "fina_indicator": self.validate_financials(fina_indicator, "fina_indicator", code),
        }

        all_warnings = []
        for key, result in results.items():
            all_warnings.extend(result.get("warnings", []))

        all_valid = all(r["is_valid"] for r in results.values())

        return {
            "is_valid": all_valid,
            "total_warnings": len(all_warnings),
            "warnings": all_warnings,
            "details": results,
        }