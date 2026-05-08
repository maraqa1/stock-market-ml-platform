from __future__ import annotations

import pandas as pd


def portfolio_summary(positions: pd.DataFrame) -> dict:
    if positions.empty:
        return {
            "position_count": 0,
            "market_value": 0.0,
            "cost_basis": 0.0,
            "unrealized_pl": 0.0,
        }
    frame = positions.copy()
    for column in ["market_value", "cost_basis", "unrealized_pl"]:
        frame[column] = pd.to_numeric(frame.get(column, 0), errors="coerce").fillna(0)
    return {
        "position_count": int(len(frame)),
        "market_value": float(frame["market_value"].sum()),
        "cost_basis": float(frame["cost_basis"].sum()),
        "unrealized_pl": float(frame["unrealized_pl"].sum()),
    }
