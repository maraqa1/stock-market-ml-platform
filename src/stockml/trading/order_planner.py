from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR, latest_file
from stockml.trading.config import AlpacaConfig
from stockml.trading.order_builder import order_row
from stockml.trading.trade_quality_gate import apply_trade_quality_gate


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
    if "diagnostic_only" in frame.columns:
        frame = frame[~frame["diagnostic_only"].astype(str).str.lower().isin({"true", "1", "yes"})]
    if frame.empty:
        return frame
    frame = frame[frame["trade_action"].apply(_valid_action)]
    if frame.empty:
        return frame
    frame["side_probability"] = pd.to_numeric(frame["side_probability"], errors="coerce")
    frame["probability_edge"] = pd.to_numeric(frame["probability_edge"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["risk_adjusted_score"] = pd.to_numeric(frame.get("risk_adjusted_score", 0), errors="coerce").fillna(0)
    frame = frame[
        frame["side_probability"].ge(config.min_side_probability)
        & frame["probability_edge"].abs().ge(config.min_abs_probability_edge)
    ]
    if frame.empty:
        return frame
    frame["_sort_score"] = frame["risk_adjusted_score"].abs()
    frame = frame.sort_values("_sort_score", ascending=False)
    frame = _limit_sector_concentration(frame, config)
    return frame.head(config.max_orders).drop(columns=["_sort_score"])


def _limit_sector_concentration(frame: pd.DataFrame, config: AlpacaConfig) -> pd.DataFrame:
    if "sector" not in frame.columns or frame.empty:
        return frame
    max_fraction = min(max(config.max_sector_fraction, 0.0), 1.0)
    if max_fraction <= 0:
        return frame.iloc[0:0]
    max_per_sector = max(1, int(config.max_orders * max_fraction))
    selected = []
    sector_counts: dict[str, int] = {}
    for _, row in frame.iterrows():
        sector = str(row.get("sector") or "Unknown")
        if sector_counts.get(sector, 0) >= max_per_sector:
            continue
        selected.append(row)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= config.max_orders:
            break
    if not selected:
        return frame.iloc[0:0]
    return pd.DataFrame(selected)


def build_order_plan(
    signals: pd.DataFrame,
    config: AlpacaConfig,
    price_snapshot: Optional[pd.DataFrame] = None,
    metadata: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    filtered = filter_tradeable_signals(signals, config)
    if filtered.empty:
        return pd.DataFrame()
    gated = apply_trade_quality_gate(filtered, config, price_snapshot=price_snapshot, metadata=metadata)
    running_notional = 0.0
    for idx, row in gated.iterrows():
        if str(row.get("trade_quality_status", "")).lower() != "approved":
            continue
        approved = float(row.get("approved_notional") or 0)
        if running_notional + approved > config.max_total_notional:
            gated.loc[idx, "trade_quality_status"] = "rejected"
            gated.loc[idx, "trade_quality_reason"] = "max_total_notional_breached"
            gated.loc[idx, "approved_notional"] = 0.0
            gated.loc[idx, "suggested_quantity"] = 0
        else:
            running_notional += approved
    return pd.DataFrame([order_row(row, config) for _, row in gated.iterrows()])
