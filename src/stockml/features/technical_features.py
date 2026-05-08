from __future__ import annotations

import numpy as np
import pandas as pd


def add_technical_features(prices: pd.DataFrame) -> pd.DataFrame:
    out = prices.sort_values(["ticker", "date"]).copy()
    group = out.groupby("ticker", group_keys=False)

    for days in [1, 5, 10, 20, 60]:
        out[f"return_{days}d"] = group["adj_close"].pct_change(days, fill_method=None)

    out["high_20d"] = group["high"].transform(lambda s: s.rolling(20, min_periods=5).max())
    out["low_20d"] = group["low"].transform(lambda s: s.rolling(20, min_periods=5).min())
    out["distance_from_20d_high"] = out["adj_close"] / out["high_20d"] - 1.0
    out["distance_from_20d_low"] = out["adj_close"] / out["low_20d"] - 1.0

    for days in [20, 50, 200]:
        out[f"sma_{days}"] = group["adj_close"].transform(lambda s: s.rolling(days, min_periods=max(5, days // 4)).mean())

    out["sma_gap_20_50"] = out["sma_20"] / out["sma_50"] - 1.0
    out["sma_gap_50_200"] = out["sma_50"] / out["sma_200"] - 1.0
    out["rsi_14"] = group["adj_close"].transform(_rsi_14)
    macd = group["adj_close"].transform(lambda s: s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean())
    out["macd"] = macd
    out["macd_signal"] = group["adj_close"].transform(
        lambda s: (s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()).ewm(span=9, adjust=False).mean()
    )
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    return out


def _rsi_14(series: pd.Series) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)
