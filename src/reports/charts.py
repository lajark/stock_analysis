"""可视化图表 — Plotly 交互式 K 线图和技术指标图。

生成的图表以 HTML 嵌入 Markdown 报告，或独立保存为 PNG/HTML。
"""

from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def create_kline_chart(
    daily: pd.DataFrame,
    title: str = "",
    output_path: Optional[str] = None,
) -> str:
    """生成 K 线图 + 技术指标（MACD/RSI/成交量）。

    Args:
        daily: 日线行情（含技术指标列）
        title: 图表标题
        output_path: 输出 HTML 路径，None 则返回 HTML 字符串

    Returns:
        HTML 字符串或文件路径。
    """
    df = daily.copy()
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 4 行子图：K线+布林、成交量、MACD、RSI
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.45, 0.15, 0.20, 0.20],
        subplot_titles=("K线 & MA", "成交量", "MACD", "RSI"),
    )

    # --- Row 1: K线 + 布林带 + MA ---
    fig.add_trace(
        go.Candlestick(
            x=df["trade_date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K线",
            increasing_line_color="#ef5350",
            decreasing_line_color="#26a69a",
        ),
        row=1, col=1,
    )

    # MA 线
    ma_colors = {"ma_5": "#FF9800", "ma_20": "#2196F3", "ma_60": "#9C27B0"}
    for col, color in ma_colors.items():
        if col in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["trade_date"], y=df[col],
                    mode="lines", name=col.upper(),
                    line=dict(color=color, width=1),
                ),
                row=1, col=1,
            )

    # 布林带
    if "bb_upper" in df.columns and "bb_lower" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["trade_date"], y=df["bb_upper"],
                mode="lines", name="布林上轨",
                line=dict(color="gray", width=0.5, dash="dash"),
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df["trade_date"], y=df["bb_lower"],
                mode="lines", name="布林下轨",
                line=dict(color="gray", width=0.5, dash="dash"),
                fill="tonexty", fillcolor="rgba(128,128,128,0.05)",
            ),
            row=1, col=1,
        )

    # --- Row 2: 成交量 ---
    colors = ["#ef5350" if df["close"].iloc[i] >= df["open"].iloc[i] else "#26a69a"
              for i in range(len(df))]
    fig.add_trace(
        go.Bar(x=df["trade_date"], y=df["volume"], name="成交量",
               marker_color=colors, showlegend=False),
        row=2, col=1,
    )

    # --- Row 3: MACD ---
    if "macd" in df.columns:
        fig.add_trace(
            go.Scatter(x=df["trade_date"], y=df["macd"],
                       mode="lines", name="MACD", line=dict(color="#2196F3", width=1)),
            row=3, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df["trade_date"], y=df["macd_signal"],
                       mode="lines", name="Signal", line=dict(color="#FF9800", width=1)),
            row=3, col=1,
        )
        # 柱状图
        hist_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in df["macd_histogram"]]
        fig.add_trace(
            go.Bar(x=df["trade_date"], y=df["macd_histogram"],
                   name="Histogram", marker_color=hist_colors, showlegend=False),
            row=3, col=1,
        )

    # --- Row 4: RSI + KDJ ---
    if "rsi" in df.columns:
        fig.add_trace(
            go.Scatter(x=df["trade_date"], y=df["rsi"],
                       mode="lines", name="RSI", line=dict(color="#9C27B0", width=1.5)),
            row=4, col=1,
        )
        # 超买超卖线
        fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.3, row=4, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.3, row=4, col=1)

    if "kdj_k" in df.columns:
        fig.add_trace(
            go.Scatter(x=df["trade_date"], y=df["kdj_k"],
                       mode="lines", name="K", line=dict(color="#FF5722", width=1)),
            row=4, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df["trade_date"], y=df["kdj_d"],
                       mode="lines", name="D", line=dict(color="#FF9800", width=1)),
            row=4, col=1,
        )

    # 布局
    fig.update_layout(
        title=title or "股票技术分析",
        height=900,
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    fig.update_yaxes(title_text="RSI/KDJ", row=4, col=1)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(output_path)
        return output_path
    else:
        return fig.to_html()


def create_comparison_chart(
    stocks_data: dict[str, pd.DataFrame],
    metric: str = "close",
    title: str = "多股票对比",
    output_path: Optional[str] = None,
) -> str:
    """生成多股票对比图（归一化价格走势）。

    Args:
        stocks_data: {股票名称: 日线DataFrame}
        metric: 对比指标 (close/open/volume)
        title: 图表标题
        output_path: 输出路径
    """
    fig = go.Figure()

    for name, df in stocks_data.items():
        if df.empty:
            continue
        if "trade_date" in df.columns:
            df = df.copy()
            df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 归一化到 100
        values = df[metric].values
        normalized = values / values[0] * 100

        fig.add_trace(go.Scatter(
            x=df["trade_date"],
            y=normalized,
            mode="lines",
            name=name,
        ))

    fig.update_layout(
        title=title,
        height=500,
        template="plotly_white",
        hovermode="x unified",
        yaxis_title=f"{metric} (归一化, 基准=100)",
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(output_path)
        return output_path
    else:
        return fig.to_html()


def create_valuation_chart(
    daily: pd.DataFrame,
    title: str = "",
    output_path: Optional[str] = None,
) -> str:
    """生成估值分位图（收盘价 + 分位区间）。"""
    df = daily.copy()
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"])

    closes = df["close"].values
    current = closes[-1]
    pct_rank = (closes <= current).mean() * 100

    fig = go.Figure()

    # 收盘价走势
    fig.add_trace(go.Scatter(
        x=df["trade_date"], y=closes,
        mode="lines", name="收盘价",
        line=dict(color="#2196F3", width=1.5),
    ))

    # 当前位置
    fig.add_hline(
        y=current, line_dash="dash", line_color="red",
        annotation_text=f"当前 ({current:.2f})",
    )

    fig.update_layout(
        title=title or f"价格走势 (当前分位: {pct_rank:.1f}%)",
        height=400,
        template="plotly_white",
        hovermode="x unified",
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(output_path)
        return output_path
    else:
        return fig.to_html()