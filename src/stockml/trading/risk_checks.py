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
        return "high"
    if dollar_volume >= 10_000_000 and volume >= 100_000:
        return "medium"
    if dollar_volume >= 5_000_000 and volume >= 50_000:
        return "thin"
    return "illiquid"


def risk_tier(row: pd.Series) -> str:
    vol = str(row.get("volatility_tier", ""))
    avg_dollar_volume = numeric(row.get("avg_dollar_volume_20d"), default=0)
    price = numeric(row.get("current_price"), default=0)
    market_cap = numeric(row.get("market_cap", 0))
    if vol == "extreme":
        return "reject"
    if market_cap >= 5_000_000_000 and avg_dollar_volume >= 50_000_000 and price >= 10:
        return "high_quality"
    if market_cap >= 1_000_000_000 and avg_dollar_volume >= 10_000_000 and price >= 5:
        return "medium"
    if market_cap >= 300_000_000 and avg_dollar_volume >= 5_000_000 and price >= 5:
        return "speculative"
    return "reject"


def _missing(value: object) -> bool:
    parsed = pd.to_numeric(value, errors="coerce")
    return bool(pd.isna(parsed))


def eligibility_reasons(row: pd.Series, config: AlpacaConfig) -> list[str]:
    reasons: list[str] = []
    action = str(row.get("trade_action", "")).strip().lower()
    if action not in {"long", "short"}:
        reasons.append("not_long_or_short")
    if str(row.get("model_status", row.get("decision_grade", ""))).strip().lower() == "diagnostic_only":
        if "diagnostic_paper_candidate" not in str(row.get("signal_reason", "")).strip().lower():
            reasons.append("model_not_decision_grade")
    if str(row.get("diagnostic_only", "")).strip().lower() in {"true", "1", "yes"}:
        if "diagnostic_paper_candidate" not in str(row.get("signal_reason", "")).strip().lower():
            reasons.append("model_not_decision_grade")
    no_decision_reason = str(row.get("no_decision_reason", "") or "").strip()
    if no_decision_reason and no_decision_reason.lower() not in {"nan", "none", "not provided"}:
        reasons.append("no_decision_reason_present")
    if action == "short" and not config.allow_short_selling:
        reasons.append("shorting_disabled")
    return reasons


def quality_reasons(row: pd.Series, config: AlpacaConfig) -> list[str]:
    reasons: list[str] = []
    price_raw = row.get("current_price")
    price = numeric(price_raw, default=-1)
    open_price = numeric(row.get("open_price"), default=0)
    low = numeric(row.get("intraday_low"), default=0)
    high = numeric(row.get("intraday_high"), default=0)
    intraday_return = numeric(row.get("intraday_return_from_open"), default=0)
    expected = numeric(row.get("expected_trade_return"), default=0)
    risk_score = numeric(row.get("risk_adjusted_score"), default=0)

    if _missing(price_raw):
        reasons.append("current_price_missing")
    elif price <= 0:
        reasons.append("current_price_invalid")
    elif price < config.min_trade_price:
        reasons.append("price_below_minimum")

    if _missing(row.get("market_cap")):
        reasons.append("market_cap_missing")
    elif numeric(row.get("market_cap"), default=0) < config.min_market_cap:
        reasons.append("market_cap_below_minimum")

    if _missing(row.get("avg_dollar_volume_20d")):
        reasons.append("avg_dollar_volume_missing")
    elif numeric(row.get("avg_dollar_volume_20d"), default=0) < config.min_avg_dollar_volume_20d:
        reasons.append("liquidity_below_minimum")

    if _missing(row.get("volatility_20d", row.get("volatility_60d"))):
        reasons.append("volatility_missing")
    elif row.get("volatility_tier") == "extreme":
        reasons.append("volatility_extreme")

    if intraday_return < -0.08:
        reasons.append("intraday_move_extreme_negative")
    range_width = high - low
    position = (price - low) / range_width if range_width > 0 else 0.5
    if open_price > 0 and intraday_return < -0.03 and position < 0.20:
        reasons.append("bottom_intraday_range_after_gap_down")
    if expected < config.min_expected_trade_return:
        reasons.append("expected_trade_return_below_threshold")
    if risk_score < config.min_risk_adjusted_score:
        reasons.append("risk_adjusted_score_below_threshold")
    return reasons


def reject_reasons(row: pd.Series, config: AlpacaConfig) -> list[str]:
    return [*eligibility_reasons(row, config), *quality_reasons(row, config)]
