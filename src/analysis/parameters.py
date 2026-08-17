"""Explicit, serializable parameters shared by analysis and backtests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisParameters:
    """Technical-indicator parameters with deterministic validation."""

    ma_periods: tuple[int, ...] = (5, 10, 20, 60)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    kdj_n: int = 9
    kdj_m1: int = 3
    kdj_m2: int = 3

    def __post_init__(self) -> None:
        periods = tuple(int(period) for period in self.ma_periods)
        if not periods or any(period <= 0 for period in periods):
            raise ValueError("ma_periods 必须包含正整数")
        if len(set(periods)) != len(periods):
            raise ValueError("ma_periods 不得重复")
        object.__setattr__(self, "ma_periods", periods)
        positive_fields = (
            "rsi_period",
            "macd_fast",
            "macd_slow",
            "macd_signal",
            "bollinger_period",
            "kdj_n",
            "kdj_m1",
            "kdj_m2",
        )
        for field_name in positive_fields:
            if int(getattr(self, field_name)) <= 0:
                raise ValueError(f"{field_name} 必须为正整数")
        if float(self.bollinger_std) <= 0:
            raise ValueError("bollinger_std 必须为正数")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> AnalysisParameters:
        """Build parameters from YAML/JSON-like mappings."""
        known = {
            field_name: values[field_name]
            for field_name in cls.__dataclass_fields__
            if field_name in values
        }
        if "ma_periods" in known:
            known["ma_periods"] = tuple(known["ma_periods"])
        return cls(**known)

    @classmethod
    def from_config(cls, config: Any) -> AnalysisParameters:
        """Build parameters from the existing AnalysisConfig object."""
        return cls.from_mapping(
            {
                field_name: getattr(config, field_name)
                for field_name in cls.__dataclass_fields__
                if hasattr(config, field_name)
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible values for run records and cache keys."""
        return {
            "ma_periods": list(self.ma_periods),
            "rsi_period": self.rsi_period,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "bollinger_period": self.bollinger_period,
            "bollinger_std": self.bollinger_std,
            "kdj_n": self.kdj_n,
            "kdj_m1": self.kdj_m1,
            "kdj_m2": self.kdj_m2,
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
