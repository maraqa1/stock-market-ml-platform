from __future__ import annotations

import pandas as pd

TARGET_COLUMNS = [
    "target_return_5d",
    "target_return_10d",
    "target_sector_relative_return_5d",
    "target_sector_relative_return_10d",
    "target_realized_volatility_5d",
    "target_vol_adjusted_return_5d",
    "target_decay_weighted_return_5d",
    "target_rank_pct_by_date_5d",
    "target_rank_pct_by_date_decay_5d",
    "target_top_quintile_5d",
    "target_bottom_quintile_5d",
    "target_trade_label_5d",
    "target_trade_label_tier_5d",
]


def add_ranking_targets(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["ticker", "date"]).copy()
    group = out.groupby("ticker", group_keys=False)
    future_5 = group["adj_close"].shift(-5) / out["adj_close"] - 1.0
    future_10 = group["adj_close"].shift(-10) / out["adj_close"] - 1.0
    out["target_return_5d"] = future_5
    out["target_return_10d"] = future_10

    sector_future_5 = out.groupby(["sector", "date"])["target_return_5d"].transform("median")
    sector_future_10 = out.groupby(["sector", "date"])["target_return_10d"].transform("median")
    out["target_sector_relative_return_5d"] = out["target_return_5d"] - sector_future_5
    out["target_sector_relative_return_10d"] = out["target_return_10d"] - sector_future_10

    forward_daily = pd.concat(
        [
            group["adj_close"].shift(-day) / group["adj_close"].shift(-(day - 1)) - 1.0
            for day in range(1, 6)
        ],
        axis=1,
    )
    forward_daily.columns = [f"__forward_return_day_{day}" for day in range(1, 6)]
    out["target_realized_volatility_5d"] = forward_daily.std(axis=1, skipna=False)
    safe_vol = out["target_realized_volatility_5d"].replace(0, pd.NA)
    out["target_vol_adjusted_return_5d"] = out["target_sector_relative_return_5d"] / safe_vol
    decay_weights = [0.50, 0.25, 0.15, 0.07, 0.03]
    out["target_decay_weighted_return_5d"] = forward_daily.mul(decay_weights, axis=1).sum(axis=1, min_count=5)

    out["target_rank_pct_by_date_5d"] = out.groupby("date")["target_return_5d"].rank(pct=True)
    out["target_rank_pct_by_date_decay_5d"] = out.groupby("date")["target_decay_weighted_return_5d"].rank(pct=True)
    out["target_top_quintile_5d"] = out["target_rank_pct_by_date_5d"] >= 0.8
    out["target_bottom_quintile_5d"] = out["target_rank_pct_by_date_5d"] <= 0.2
    out["target_trade_label_5d"] = "Neutral"
    out["target_trade_label_tier_5d"] = "Neutral"
    long_mask = (
        out["target_top_quintile_5d"]
        & (out["target_return_5d"] > 0)
        & (out["target_sector_relative_return_5d"] > 0)
    )
    short_mask = (
        out["target_bottom_quintile_5d"]
        & (out["target_return_5d"] < 0)
        & (out["target_sector_relative_return_5d"] < 0)
    )
    out.loc[long_mask, "target_trade_label_5d"] = "Long"
    out.loc[short_mask, "target_trade_label_5d"] = "Short"
    out.loc[long_mask, "target_trade_label_tier_5d"] = "Strong Long"
    out.loc[short_mask, "target_trade_label_tier_5d"] = "Strong Short"
    weak_long_mask = (
        out["target_top_quintile_5d"]
        & ~long_mask
        & ((out["target_return_5d"] > 0) | (out["target_sector_relative_return_5d"] > 0))
    )
    weak_short_mask = (
        out["target_bottom_quintile_5d"]
        & ~short_mask
        & ((out["target_return_5d"] < 0) | (out["target_sector_relative_return_5d"] < 0))
    )
    out.loc[weak_long_mask, "target_trade_label_tier_5d"] = "Weak Long"
    out.loc[weak_short_mask, "target_trade_label_tier_5d"] = "Weak Short"
    return out


def leakage_columns(columns: list[str]) -> list[str]:
    prefixes = ["target_", "future_", "forward_", "realized_", "outcome_", "prediction_", "signal_"]
    blocked = {"trade_action", "validation_fold", "test_fold", "train_fold"}
    return [
        col for col in columns
        if any(col.startswith(prefix) for prefix in prefixes) or col in blocked or col.startswith("model_")
    ]


def model_feature_columns(columns: list[str]) -> list[str]:
    return [col for col in columns if col not in leakage_columns(columns)]
