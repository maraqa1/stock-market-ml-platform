from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from stockml.trading.per_symbol_forecast.derived import current_price, is_short, model_score, num, text


DEFAULT_STOP_MULTIPLIER = 1.0
DEFAULT_TAKE_PROFIT_MULTIPLIER = 1.5
DEFAULT_MOVE_VOL_MULTIPLIER = 1.5


@dataclass(frozen=True)
class ForecastBounds:
    reasonable_max_1d_return_bps: float = 200.0
    reasonable_max_5d_return_bps: float = 500.0
    reasonable_max_move_bps: float = 1000.0
    suspicious_warn_threshold_bps: float = 300.0
    cap_at_max: bool = True
    max_reasonable_slope_bps_per_unit: float = 1000.0


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


def return_value_to_bps(value: float | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if pd.isna(parsed):
        return None
    if abs(parsed) <= 0.25:
        return parsed * 10000.0
    return parsed * 100.0


def rank_to_return_slope_bps(history: pd.DataFrame, horizon_col: str = "target_return_5d") -> float:
    slope = rank_to_return_slope(history, horizon_col)
    return return_value_to_bps(slope) or 0.0


def assert_slope_is_sane(slope_bps_per_score_unit: float, bounds: ForecastBounds | None = None) -> None:
    cfg = bounds or ForecastBounds()
    if abs(slope_bps_per_score_unit) > cfg.max_reasonable_slope_bps_per_unit:
        raise ValueError("per_symbol_forecast_slope_units_out_of_bounds")


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


def expected_return_bps(row: dict[str, Any], slope_5d_bps: float = 0.0) -> float | None:
    value = num(row.get("expected_trade_return"))
    if value is not None:
        return return_value_to_bps(value)
    score = model_score(row)
    if score is None:
        return None
    if not slope_5d_bps:
        return None
    expected = score * slope_5d_bps
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


def calibrated_move_bps(raw_move_bps: float | None, volatility: float | None, multiplier: float = DEFAULT_MOVE_VOL_MULTIPLIER) -> float | None:
    if raw_move_bps is None:
        return None
    if volatility is None or volatility <= 0:
        return raw_move_bps
    return min(raw_move_bps, abs(volatility) * 10000.0 * multiplier)


def capped_bps(value: float | None, limit: float, *, cap_at_max: bool = True) -> tuple[float | None, bool, float | None]:
    if value is None:
        return None, False, None
    if abs(value) <= limit:
        return value, False, None
    if not cap_at_max:
        return None, True, value
    return (limit if value > 0 else -limit), True, value


def forecast_risk_penalty(row: dict[str, Any], stop_bps: float) -> float:
    penalty = 0.0
    volatility = text(row.get("volatility_tier")).lower()
    liquidity = text(row.get("liquidity_tier")).lower()
    if volatility == "extreme":
        penalty -= 40.0
    elif volatility == "high":
        penalty -= 20.0
    if liquidity == "thin":
        penalty -= 20.0
    elif liquidity and liquidity not in {"high", "medium"}:
        penalty -= 30.0
    if stop_bps >= 1000:
        penalty -= 20.0
    elif stop_bps >= 700:
        penalty -= 10.0
    return penalty


def confirmation_quality(confirmation_score: float | None, risk_penalty: float) -> str:
    adjusted = (confirmation_score or 0.0) + risk_penalty
    if adjusted >= 80:
        return "high"
    if adjusted >= 55:
        return "medium"
    return "low"


def operator_priority(quality: str, risk_penalty: float, confirmation: str) -> str:
    if confirmation == "conflicted" or quality == "low":
        return "avoid"
    if quality == "high" and risk_penalty >= -20:
        return "high"
    return "watch"


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


def statistical_fields(row: dict[str, Any], slope_5d: float = 0.0, bounds: ForecastBounds | None = None) -> dict[str, Any]:
    cfg = bounds or ForecastBounds()
    spread = num(row.get("spread_bps"))
    vol = num(row.get("volatility_20d") or row.get("volatility_60d"))
    score = model_score(row)
    slope_5d_bps = return_value_to_bps(slope_5d) if abs(slope_5d) <= 10 else slope_5d
    assert_slope_is_sane(slope_5d_bps or 0.0, cfg)
    pre_cap_5d = expected_return_bps(row, slope_5d_bps=slope_5d_bps or 0.0)
    expected_5d, cap_5d, pre_cap_value = capped_bps(pre_cap_5d, cfg.reasonable_max_5d_return_bps, cap_at_max=cfg.cap_at_max)
    pre_cap_1d = expected_5d / 5.0 if expected_5d is not None else None
    expected_1d, cap_1d, _ = capped_bps(pre_cap_1d, cfg.reasonable_max_1d_return_bps, cap_at_max=cfg.cap_at_max)
    pre_cap_move = abs(expected_5d) if expected_5d is not None else None
    expected_move, cap_move, _ = capped_bps(pre_cap_move, cfg.reasonable_max_move_bps, cap_at_max=cfg.cap_at_max)
    cap_applied = bool(cap_5d or cap_1d or cap_move)
    vol_bps = abs(vol or 0.0) * 10000.0
    calibrated_move = calibrated_move_bps(expected_move, vol)
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
    risk_penalty = forecast_risk_penalty(row, stop_bps)
    profitability = None if calibrated_move is None or risk_adjusted is None else calibrated_move + risk_adjusted + risk_penalty
    return {
        "direction_context": direction_context(row, expected_5d),
        "direction_basis": direction_basis(row, expected_5d),
        "expected_1d_return_bps": expected_1d,
        "expected_5d_return_bps": expected_5d,
        "expected_move_bps": expected_move,
        "expected_move_bps_calibrated": calibrated_move,
        "cap_applied": cap_applied,
        "pre_cap_expected_5d_bps": pre_cap_value,
        "units_audit": {
            "internal_return_unit": "bps",
            "expected_trade_return_interpretation": "raw_ratio_when_abs_le_0.25_else_percent_points",
            "slope_unit": "bps_per_score_unit",
            "bounds": asdict(cfg),
        },
        "magnitude_bucket": magnitude_bucket(calibrated_move),
        "downside_risk_bps": vol_bps,
        "upside_risk_bps": vol_bps,
        "volatility_adjusted_score": vol_adj,
        "spread_penalty": spread_adj,
        "liquidity_penalty": liquidity_adj,
        "risk_adjusted_forecast_score": risk_adjusted,
        "expected_profitability_score": profitability,
        "forecast_risk_penalty": risk_penalty,
        "suggested_stop_bps": stop_bps,
        "suggested_take_profit_bps": take_profit_bps,
        "invalidation_level": invalidation,
        "forecast_reason": forecast_reason(row, expected_5d, vol_adj),
        "regime_label": regime_label(row),
    }
