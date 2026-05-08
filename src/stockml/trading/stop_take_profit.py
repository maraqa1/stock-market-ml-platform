from __future__ import annotations


def stop_take_profit_prices(current_price: float, side: str, volatility_tier: str) -> dict[str, float | int]:
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    stop_pct = 0.05 if volatility_tier in {"high", "speculative"} else 0.03
    take_pct = 0.10 if volatility_tier in {"high", "speculative"} else 0.06
    holding_days = 10 if volatility_tier in {"high", "speculative"} else 5
    if side == "sell":
        stop = current_price * (1 + stop_pct)
        take = current_price * (1 - take_pct)
    else:
        stop = current_price * (1 - stop_pct)
        take = current_price * (1 + take_pct)
    return {
        "stop_loss_price": round(stop, 4),
        "take_profit_price": round(take, 4),
        "max_holding_days": holding_days,
    }
