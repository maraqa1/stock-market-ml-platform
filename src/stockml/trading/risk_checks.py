from __future__ import annotations

import pandas as pd

from stockml.trading.config import AlpacaConfig


def numeric(value: object, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(parsed) else parsed)


def volatility_tier(row: pd.Series) -> str:
    vol = numeric(row.get("volatility_20d", row.get("volatility_60d", 0)))
    if vol >= 0.12:
        return "extreme"
    if vol >= 0.07:
        return "high"
    if vol >= 0.035:
        return "medium"
    return "low"


def liquidity_tier(row: pd.Series) -> str:
    dollar_volume = numeric(row.get("avg_dollar_volume_20d", 0))
    volume = numeric(row.get("intraday_volume", row.get("volume", 0)))
    if dollar_volume >= 50_000_000 and volume >= 1_000_000:
        return "large_liquid"
    if dollar_volume >= 5_000_000 and volume >= 100_000:
        return "mid"
    if dollar_volume >= 1_000_000 and volume >= 50_000:
        return "thin"
    return "illiquid"


def risk_tier(row: pd.Series) -> str:
    vol = str(row.get("volatility_tier", ""))
    liq = str(row.get("liquidity_tier", ""))
    market_cap = numeric(row.get("market_cap", 0))
    if vol == "extreme" or liq == "illiquid":
        return "reject"
    if market_cap >= 10_000_000_000 and liq == "large_liquid" and vol in {"low", "medium"}:
        return "large_liquid"
    if market_cap >= 1_000_000_000 and liq in {"large_liquid", "mid"} and vol in {"low", "medium"}:
        return "mid_risk"
    return "speculative"


def reject_reasons(row: pd.Series, config: AlpacaConfig) -> list[str]:
    reasons: list[str] = []
    price = numeric(row.get("current_price"), default=-1)
    open_price = numeric(row.get("open_price"), default=0)
    low = numeric(row.get("intraday_low"), default=0)
    high = numeric(row.get("intraday_high"), default=0)
    intraday_return = numeric(row.get("intraday_return_from_open"), default=0)
    expected = numeric(row.get("expected_trade_return"), default=0)
    risk_score = numeric(row.get("risk_adjusted_score"), default=0)
    transaction_cost = float(config.transaction_cost_bps) / 10_000
    if price <= 0:
        reasons.append("missing_or_invalid_current_price")
    elif price < config.min_trade_price:
        reasons.append("price_below_minimum")
    if intraday_return < -0.08:
        reasons.append("intraday_drop_below_minus_8pct")
    range_width = high - low
    position = (price - low) / range_width if range_width > 0 else 0.5
    if open_price > 0 and intraday_return < -0.03 and position < 0.20:
        reasons.append("bottom_intraday_range_after_gap_down")
    if numeric(row.get("intraday_volume"), default=0) < config.min_intraday_volume:
        reasons.append("intraday_volume_below_minimum")
    if numeric(row.get("market_cap"), default=0) < config.min_market_cap:
        reasons.append("market_cap_below_minimum")
    if row.get("volatility_tier") == "extreme":
        reasons.append("extreme_volatility")
    if expected < transaction_cost:
        reasons.append("expected_return_below_transaction_cost")
    if risk_score < config.min_risk_adjusted_score:
        reasons.append("risk_adjusted_score_below_threshold")
    return reasons
