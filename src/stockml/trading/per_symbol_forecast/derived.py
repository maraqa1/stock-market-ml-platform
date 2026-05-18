from __future__ import annotations

from typing import Any

import pandas as pd


def num(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(parsed)


def text(value: Any) -> str:
    clean = str(value or "").strip()
    return "" if clean.lower() in {"nan", "none"} else clean


def is_short(row: dict[str, Any]) -> bool:
    side = text(row.get("side")).lower()
    action = text(row.get("trade_action")).lower()
    return side == "sell" or action == "short"


def canonical_symbol(row: dict[str, Any]) -> str:
    return text(row.get("symbol") or row.get("ticker") or row.get("yahoo_ticker")).upper()


def current_price(row: dict[str, Any]) -> float | None:
    return num(row.get("current_price") or row.get("last_price") or row.get("close"))


def model_score(row: dict[str, Any]) -> float | None:
    return num(row.get("model_score") or row.get("rank_score") or row.get("risk_adjusted_score") or row.get("confidence_score"))


def derived_fields(row: dict[str, Any], generated_at: str) -> dict[str, Any]:
    is_open_position = bool(row.get("__is_open_position"))
    return {
        "generated_at": generated_at,
        "symbol": canonical_symbol(row),
        "forecast_scope": text(row.get("__forecast_scope") or ("open_position" if is_open_position else "candidate")),
        "is_open_position": is_open_position,
        "position_qty": num(row.get("qty") or row.get("position_qty")),
        "position_entry_price": num(row.get("avg_entry_price") or row.get("entry_price") or row.get("position_entry_price")),
        "position_unrealized_plpc": num(row.get("unrealized_plpc") or row.get("position_unrealized_plpc")),
        "side": text(row.get("side")),
        "current_trade_action": text(row.get("trade_action") or row.get("action") or row.get("candidate_status")),
        "candidate_rank": row.get("candidate_rank") or row.get("rank"),
        "model_score": model_score(row),
        "model_risk_adjusted_score": num(row.get("risk_adjusted_score")),
        "meta_label_probability": num(row.get("meta_label_probability")),
        "current_price": current_price(row),
        "vwap_distance_bps": num(row.get("vwap_distance_bps")),
        "intraday_range_position": num(row.get("price_position_in_intraday_range") or row.get("intraday_range_position")),
        "spread_bps": num(row.get("spread_bps")),
        "dollar_volume_today": num(row.get("dollar_volume_today") or row.get("intraday_dollar_volume")),
        "liquidity_tier": text(row.get("liquidity_tier")),
        "volatility_tier": text(row.get("volatility_tier")),
    }
