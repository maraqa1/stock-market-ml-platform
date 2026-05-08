from __future__ import annotations


def stop_take_profit_prices(
    current_price: float,
    side: str,
    volatility_tier: str = "low",
    risk_tier: str = "",
    default_stop_loss_pct: float = 0.03,
    default_take_profit_pct: float = 0.06,
    high_volatility_stop_loss_pct: float = 0.05,
    high_volatility_take_profit_pct: float = 0.10,
    max_holding_days: int = 10,
) -> dict[str, float | int]:
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    wide = volatility_tier in {"high", "extreme"} or risk_tier == "speculative"
    stop_pct = high_volatility_stop_loss_pct if wide else default_stop_loss_pct
    take_pct = high_volatility_take_profit_pct if wide else default_take_profit_pct
    holding_days = max_holding_days if wide else min(5, max_holding_days)
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
