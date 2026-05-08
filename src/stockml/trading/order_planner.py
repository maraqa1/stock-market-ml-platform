from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR, latest_file
from stockml.trading.config import AlpacaConfig


REQUIRED_SIGNAL_COLUMNS = {
    "ticker",
    "trade_action",
    "side_probability",
    "probability_edge",
    "close",
}


def latest_signal_table(path: Optional[Path] = None) -> pd.DataFrame:
    signal_file = path or latest_file(MODEL_OUTPUTS_DIR, "advanced_model_signal_table_*.csv")
    if signal_file is None or not signal_file.exists():
        return pd.DataFrame()
    return pd.read_csv(signal_file, low_memory=False)


def _valid_action(value: object) -> bool:
    return str(value or "").strip().lower() in {"long", "short"}


def _side(action: str) -> str:
    return "buy" if action.lower() == "long" else "sell"


def _notional_order(row: pd.Series, config: AlpacaConfig) -> dict:
    action = str(row["trade_action"])
    return {
        "symbol": str(row["ticker"]).upper(),
        "notional": round(float(config.max_notional_per_order), 2),
        "side": _side(action),
        "type": "market",
        "time_in_force": "day",
        "extended_hours": bool(config.extended_hours),
        "client_order_id": f"stockml-{str(row.get('date', 'latest')).replace('-', '')}-{str(row['ticker']).upper()}-{_side(action)}",
    }


def filter_tradeable_signals(signals: pd.DataFrame, config: AlpacaConfig) -> pd.DataFrame:
    if signals.empty or not REQUIRED_SIGNAL_COLUMNS.issubset(signals.columns):
        return pd.DataFrame()
    frame = signals.copy()
    frame = frame[frame["trade_action"].apply(_valid_action)]
    frame["side_probability"] = pd.to_numeric(frame["side_probability"], errors="coerce")
    frame["probability_edge"] = pd.to_numeric(frame["probability_edge"], errors="coerce")
    frame["risk_adjusted_score"] = pd.to_numeric(frame.get("risk_adjusted_score", 0), errors="coerce").fillna(0)
    frame = frame[
        frame["side_probability"].ge(config.min_side_probability)
        & frame["probability_edge"].abs().ge(config.min_abs_probability_edge)
    ]
    if frame.empty:
        return frame
    frame["_sort_score"] = frame["risk_adjusted_score"].abs()
    return frame.sort_values("_sort_score", ascending=False).head(config.max_orders).drop(columns=["_sort_score"])


def build_order_plan(signals: pd.DataFrame, config: AlpacaConfig) -> pd.DataFrame:
    filtered = filter_tradeable_signals(signals, config)
    if filtered.empty:
        return pd.DataFrame()
    rows = []
    for _, row in filtered.iterrows():
        order = _notional_order(row, config)
        rows.append(
            {
                **order,
                "trade_action": row.get("trade_action"),
                "side_probability": row.get("side_probability"),
                "probability_edge": row.get("probability_edge"),
                "risk_adjusted_score": row.get("risk_adjusted_score"),
                "signal_reason": row.get("signal_reason", ""),
            }
        )
    return pd.DataFrame(rows)

