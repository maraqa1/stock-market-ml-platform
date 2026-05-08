from __future__ import annotations

import pandas as pd


def add_sector_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["sector"] = out["sector"].fillna("Unknown")
    sector_date = out.groupby(["sector", "date"], as_index=False).agg(
        sector_return_5d=("return_5d", "median"),
        sector_return_20d=("return_20d", "median"),
    )
    out = out.merge(sector_date, on=["sector", "date"], how="left")
    out["relative_return_vs_sector_5d"] = out["return_5d"] - out["sector_return_5d"]
    out["relative_return_vs_sector_20d"] = out["return_20d"] - out["sector_return_20d"]
    out["sector_momentum_rank"] = out.groupby("date")["sector_return_20d"].rank(pct=True)
    out["sector_relative_strength_score"] = out.groupby("date")["relative_return_vs_sector_20d"].rank(pct=True).fillna(0.5)
    out["sector_relative_momentum_score"] = out["sector_relative_strength_score"]
    return out

