from __future__ import annotations

import math


def approved_notional(base_notional: float, risk_tier: str) -> float:
    if risk_tier == "large_liquid":
        multiplier = 1.0
    elif risk_tier == "mid_risk":
        multiplier = 0.5
    elif risk_tier == "speculative":
        multiplier = 0.25
    else:
        multiplier = 0.0
    return round(max(0.0, float(base_notional) * multiplier), 2)


def suggested_quantity(notional: float, current_price: float) -> int:
    if current_price <= 0 or notional <= 0:
        return 0
    return int(math.floor(float(notional) / float(current_price)))
