from __future__ import annotations

import numpy as np
import pandas as pd


def add_liquidity_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["ticker", "date"]).copy()
    out["dollar_volume"] = out["close"] * out["volume"]
    group = out.groupby("ticker", group_keys=False)
    out["avg_dollar_volume_20d"] = group["dollar_volume"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    rolling_volume = group["volume"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    out["volume_ratio_20d"] = out["volume"] / rolling_volume.replace(0, np.nan)
    out["liquidity_score"] = out.groupby("date")["avg_dollar_volume_20d"].rank(pct=True).fillna(0)
    out["volume_confirmation_score"] = out.groupby("date")["volume_ratio_20d"].rank(pct=True).fillna(0)
    return out

