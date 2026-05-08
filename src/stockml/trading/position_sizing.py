from __future__ import annotations

import math


def risk_tier_multiplier(risk_tier: str) -> float:
    if risk_tier in {"high_quality", "large_liquid"}:
        multiplier = 1.0
    elif risk_tier in {"medium", "mid_risk"}:
        multiplier = 0.5
    elif risk_tier == "speculative":
        multiplier = 0.25
    else:
        multiplier = 0.0
    return multiplier


def confidence_multiplier(side_probability: float) -> float:
    probability = float(side_probability or 0)
    if probability >= 0.75:
        return 1.0
    if probability >= 0.60:
        return 0.75
    if probability >= 0.50:
        return 0.50
    return 0.25


def base_notional(account_equity: float, max_position_pct: float, max_basket_notional: float, max_daily_orders: int) -> float:
    position_cap = max(0.0, float(account_equity) * float(max_position_pct))
    daily_order_cap = float(max_basket_notional) / max(1, int(max_daily_orders))
    return min(position_cap, daily_order_cap)


def approved_notional(base_notional: float, risk_tier: str, side_probability: float = 1.0) -> float:
    notional = float(base_notional) * risk_tier_multiplier(risk_tier) * confidence_multiplier(side_probability)
    return round(max(0.0, notional), 2)


def suggested_quantity(notional: float, current_price: float) -> int:
    if current_price <= 0 or notional <= 0:
        return 0
    return int(math.floor(float(notional) / float(current_price)))
