from __future__ import annotations

from typing import Any

import pandas as pd

from stockml.trading.per_symbol_forecast.derived import current_price, is_short, model_score, num, text


DEFAULT_STOP_MULTIPLIER = 1.0
DEFAULT_TAKE_PROFIT_MULTIPLIER = 1.5


def rank_to_return_slope(history: pd.DataFrame, horizon_col: str = "target_return_5d") -> float:
    if history.empty or "model_score" not in history.columns or horizon_col not in history.columns:
        return 0.0
    frame = history[["model_score", horizon_col]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < 3 or frame["model_score"].nunique() < 2:
        return 0.0
    cov = frame["model_score"].cov(frame[horizon_col])
    var = frame["model_score"].var()
    if not var:
        return 0.0
    return float(cov / var)


def volatility_adjusted_score(score: float | None, volatility: float | None, floor: float = 0.01, cap: float = 100.0) -> float | None:
    if score is None:
        return None
    denom = max(abs(volatility or 0.0), floor)
    return max(min(score / denom, cap), -cap)


def liquidity_penalty(row: dict[str, Any]) -> float:
    tier = text(row.get("liquidity_tier")).lower()
    if tier == "high":
        return 0.0
    if tier == "medium":
        return -0.1
    if tier == "thin":
        return -0.25
    if tier:
        return -0.5
    return 0.0


def spread_penalty(spread_bps: float | None) -> float:
    if spread_bps is None:
        return 0.0
    return -min(max(spread_bps, 0.0) / 100.0, 1.0)


def expected_return(row: dict[str, Any], slope_5d: float = 0.0) -> float | None:
    value = num(row.get("expected_trade_return"))
    if value is not None:
        return value
    score = model_score(row)
    if score is None:
        return None
    expected = score * slope_5d
    return -abs(expected) if is_short(row) else expected


def direction_context(row: dict[str, Any], expected_5d: float | None) -> str:
    action = text(row.get("trade_action")).lower()
    side = text(row.get("side")).lower()
    if action == "short" or side == "sell":
        return "short_bias"
    if action == "long" or side == "buy":
        return "long_bias"
    if expected_5d is None:
        return "unknown"
    if expected_5d > 0:
        return "long_bias"
    if expected_5d < 0:
        return "short_bias"
    return "neutral"


def direction_basis(row: dict[str, Any], expected_5d: float | None) -> str:
    if text(row.get("trade_action")) or text(row.get("side")):
        return "candidate_side_and_trade_action"
    if expected_5d is not None:
        return "expected_return_sign"
    return "unavailable"


def magnitude_bucket(expected_move_bps: float | None) -> str:
    if expected_move_bps is None:
        return "unknown"
    if expected_move_bps < 50:
        return "small"
    if expected_move_bps < 150:
        return "medium"
    return "large"


def regime_label(row: dict[str, Any]) -> str:
    tier = text(row.get("volatility_tier")).lower()
    if tier == "extreme":
        return "extreme"
    if tier == "high":
        return "elevated"
    if tier == "low":
        return "calm"
    return "normal"


def forecast_reason(row: dict[str, Any], expected_5d: float | None, vol_adj: float | None) -> str:
    rank = num(row.get("candidate_rank") or row.get("rank"))
    liquidity = text(row.get("liquidity_tier")).lower()
    volatility = text(row.get("volatility_tier")).lower()
    if text(row.get("trade_action")).lower() == "short" or is_short(row):
        return "RANK_BOTTOM_DECILE_SHORT_CANDIDATE"
    if rank is not None and rank <= 10 and liquidity == "high":
        return "RANK_TOP_DECILE_HIGH_LIQUIDITY"
    if rank is not None and rank <= 10 and volatility in {"low", "medium"}:
        return "RANK_TOP_DECILE_LOW_VOL"
    if rank is not None and rank <= 10:
        return "RANK_TOP_DECILE_WITH_MOMENTUM"
    if expected_5d and expected_5d > 0 and vol_adj and vol_adj > 0:
        return "RANK_MID_DECILE_STRONG_CONTEXT"
    return "NEUTRAL_LOW_CONVICTION"


def statistical_fields(row: dict[str, Any], slope_5d: float = 0.0) -> dict[str, Any]:
    spread = num(row.get("spread_bps"))
    vol = num(row.get("volatility_20d") or row.get("volatility_60d"))
    score = model_score(row)
    expected_5d = expected_return(row, slope_5d=slope_5d)
    expected_1d = expected_5d / 5.0 if expected_5d is not None else None
    expected_move = abs(expected_5d * 10000.0) if expected_5d is not None else None
    vol_bps = abs(vol or 0.0) * 10000.0
    stop_bps = vol_bps * DEFAULT_STOP_MULTIPLIER
    take_profit_bps = vol_bps * DEFAULT_TAKE_PROFIT_MULTIPLIER
    price = current_price(row)
    if price is None:
        invalidation = None
    elif is_short(row):
        invalidation = price * (1.0 + stop_bps / 10000.0)
    else:
        invalidation = price * (1.0 - stop_bps / 10000.0)
    vol_adj = volatility_adjusted_score(score, vol)
    spread_adj = spread_penalty(spread)
    liquidity_adj = liquidity_penalty(row)
    risk_adjusted = None if vol_adj is None else vol_adj + spread_adj + liquidity_adj
    profitability = None if expected_move is None or risk_adjusted is None else expected_move + risk_adjusted
    return {
        "direction_context": direction_context(row, expected_5d),
        "direction_basis": direction_basis(row, expected_5d),
        "expected_1d_return": expected_1d,
        "expected_5d_return": expected_5d,
        "expected_move_bps": expected_move,
        "magnitude_bucket": magnitude_bucket(expected_move),
        "downside_risk_bps": vol_bps,
        "upside_risk_bps": vol_bps,
        "volatility_adjusted_score": vol_adj,
        "spread_penalty": spread_adj,
        "liquidity_penalty": liquidity_adj,
        "risk_adjusted_forecast_score": risk_adjusted,
        "expected_profitability_score": profitability,
        "suggested_stop_bps": stop_bps,
        "suggested_take_profit_bps": take_profit_bps,
        "invalidation_level": invalidation,
        "forecast_reason": forecast_reason(row, expected_5d, vol_adj),
        "regime_label": regime_label(row),
    }
