from __future__ import annotations

import pandas as pd


TRADE_ACTIONS = {"long", "short"}


def _action_series(frame: pd.DataFrame) -> pd.Series:
    if "trade_action" not in frame.columns:
        return pd.Series("", index=frame.index, dtype="object")
    return frame["trade_action"].astype(str).str.strip().str.lower()


def add_meta_label_targets(frame: pd.DataFrame, transaction_cost_bps: float = 10.0) -> pd.DataFrame:
    """Add non-leaking meta-label target columns for historical primary signals."""
    out = frame.copy()
    actions = _action_series(out)
    target = pd.to_numeric(out.get("target_return_5d", pd.Series(0.0, index=out.index)), errors="coerce")
    cost = float(transaction_cost_bps) / 10_000.0
    out["meta_is_trade_example"] = actions.isin(TRADE_ACTIONS)
    out["meta_realized_gain"] = 0.0
    long_mask = actions.eq("long")
    short_mask = actions.eq("short")
    out.loc[long_mask, "meta_realized_gain"] = target.loc[long_mask] - cost
    out.loc[short_mask, "meta_realized_gain"] = -target.loc[short_mask] - cost
    out["meta_label"] = ((out["meta_realized_gain"] > 0) & out["meta_is_trade_example"]).astype(int)
    return out


def trade_examples(frame: pd.DataFrame) -> pd.DataFrame:
    if "meta_is_trade_example" not in frame.columns:
        return frame.iloc[0:0].copy()
    return frame[frame["meta_is_trade_example"].astype(bool)].copy()
