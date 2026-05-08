from __future__ import annotations

import pandas as pd


def add_market_context_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    market = out.groupby("date", as_index=False).agg(
        market_return_5d=("return_5d", "median"),
        market_return_20d=("return_20d", "median"),
        market_volatility_20d=("volatility_20d", "median"),
    )
    market["market_regime_score"] = market["market_return_20d"].rank(pct=True).fillna(0.5)
    market["risk_on_risk_off_flag"] = market["market_return_20d"].apply(lambda x: "risk_on" if x > 0 else "risk_off")
    return out.merge(market, on="date", how="left")

