from __future__ import annotations

import numpy as np
import pandas as pd


def add_volatility_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["ticker", "date"]).copy()
    group = out.groupby("ticker", group_keys=False)
    returns = group["adj_close"].pct_change()
    out["_daily_return"] = returns
    out["volatility_20d"] = returns.groupby(out["ticker"]).transform(lambda s: s.rolling(20, min_periods=5).std())
    out["volatility_60d"] = returns.groupby(out["ticker"]).transform(lambda s: s.rolling(60, min_periods=10).std())
    downside = returns.where(returns < 0, 0)
    out["downside_volatility_20d"] = downside.groupby(out["ticker"]).transform(lambda s: s.rolling(20, min_periods=5).std())
    rolling_max = group["adj_close"].transform(lambda s: s.rolling(60, min_periods=10).max())
    out["max_drawdown_60d"] = out["adj_close"] / rolling_max.replace(0, np.nan) - 1.0
    vol_rank = out.groupby("date")["volatility_20d"].rank(pct=True)
    out["volatility_score"] = (1 - vol_rank).clip(0, 1).fillna(0.5)
    risk_rank = out.groupby("date")["max_drawdown_60d"].rank(pct=True)
    out["risk_score"] = ((out["volatility_score"].fillna(0.5) + risk_rank.fillna(0.5)) / 2).clip(0, 1)
    return out.drop(columns=["_daily_return"])

