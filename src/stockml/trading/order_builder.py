from __future__ import annotations

import pandas as pd

from stockml.trading.config import AlpacaConfig


def side_for_action(action: str) -> str:
    return "sell" if str(action).lower() == "short" else "buy"


def order_row(row: pd.Series, config: AlpacaConfig) -> dict:
    side = side_for_action(str(row.get("trade_action", "")))
    symbol = str(row.get("ticker", row.get("symbol", ""))).upper()
    date_part = str(row.get("date", "latest")).replace("-", "")
    status = str(row.get("trade_quality_status", "")).lower()
    approved = status in {"approved", "reduced"}
    notional = float(row.get("approved_notional", 0) or 0)
    return {
        "symbol": symbol,
        "company": row.get("company", ""),
        "sector": row.get("sector", ""),
        "notional": round(notional, 2),
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "extended_hours": bool(config.extended_hours),
        "client_order_id": f"stockml-{date_part}-{symbol}-{side}",
        "trade_action": row.get("trade_action"),
        "confidence_score": row.get("confidence_score", ""),
        "side_probability": row.get("side_probability"),
        "probability_edge": row.get("probability_edge"),
        "expected_trade_return": row.get("expected_trade_return", ""),
        "risk_adjusted_score": row.get("risk_adjusted_score"),
        "signal_reason": row.get("signal_reason", ""),
        "no_decision_reason": row.get("no_decision_reason", ""),
        "close": row.get("close", ""),
        "current_price": row.get("current_price", ""),
        "open_price": row.get("open_price", ""),
        "intraday_high": row.get("intraday_high", ""),
        "intraday_low": row.get("intraday_low", ""),
        "intraday_volume": row.get("intraday_volume", ""),
        "price_position_in_intraday_range": row.get("price_position_in_intraday_range", ""),
        "intraday_return_from_open": row.get("intraday_return_from_open", ""),
        "market_cap": row.get("market_cap", ""),
        "avg_dollar_volume_20d": row.get("avg_dollar_volume_20d", ""),
        "volatility_20d": row.get("volatility_20d", ""),
        "volatility_tier": row.get("volatility_tier", ""),
        "liquidity_tier": row.get("liquidity_tier", ""),
        "risk_tier": row.get("risk_tier", ""),
        "approved_notional": notional,
        "suggested_quantity": int(row.get("suggested_quantity", 0) or 0),
        "stop_loss_price": row.get("stop_loss_price", ""),
        "take_profit_price": row.get("take_profit_price", ""),
        "max_holding_days": row.get("max_holding_days", ""),
        "trade_quality_status": status if approved else "rejected",
        "trade_quality_reason": row.get("trade_quality_reason", ""),
        "order_eligible": bool(row.get("order_eligible", approved and notional > 0)),
    }
