from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from stockml.trading.config import AlpacaConfig

VALID_SIDES = {"buy", "sell"}
VALID_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}
VALID_TIME_IN_FORCE = {"day", "gtc", "opg", "cls", "ioc", "fok"}


@dataclass(frozen=True)
class OrderValidationResult:
    valid: bool
    reason: str = "valid"


def side_for_action(action: str) -> str:
    return "sell" if str(action).lower() == "short" else "buy"


def validate_order_payload(order: dict[str, Any], max_order_notional: float | None = None) -> OrderValidationResult:
    symbol = str(order.get("symbol") or "").strip().upper()
    side = str(order.get("side") or "").strip().lower()
    order_type = str(order.get("type") or "").strip().lower()
    tif = str(order.get("time_in_force") or "").strip().lower()
    qty = order.get("qty")
    notional = order.get("notional")
    has_qty = qty not in [None, ""]
    has_notional = notional not in [None, ""]
    if not symbol:
        return OrderValidationResult(False, "missing_symbol")
    if side not in VALID_SIDES:
        return OrderValidationResult(False, "invalid_side")
    if order_type not in VALID_ORDER_TYPES:
        return OrderValidationResult(False, "invalid_order_type")
    if tif not in VALID_TIME_IN_FORCE:
        return OrderValidationResult(False, "invalid_time_in_force")
    if has_qty and has_notional:
        return OrderValidationResult(False, "both_qty_and_notional")
    if not has_qty and not has_notional:
        return OrderValidationResult(False, "missing_qty_or_notional")
    if has_qty and float(qty) <= 0:
        return OrderValidationResult(False, "non_positive_quantity")
    if has_notional and float(notional) <= 0:
        return OrderValidationResult(False, "non_positive_notional")
    if max_order_notional is not None and has_notional and float(notional) > float(max_order_notional):
        return OrderValidationResult(False, "max_order_notional_exceeded")
    if order_type == "market" and order.get("limit_price") not in [None, ""]:
        return OrderValidationResult(False, "market_order_has_limit_price")
    if order_type == "limit" and order.get("limit_price") in [None, ""]:
        return OrderValidationResult(False, "missing_limit_price")
    if order_type == "stop" and order.get("stop_price") in [None, ""]:
        return OrderValidationResult(False, "missing_stop_price")
    if order_type == "stop_limit" and (order.get("stop_price") in [None, ""] or order.get("limit_price") in [None, ""]):
        return OrderValidationResult(False, "missing_stop_limit_price")
    if order.get("extended_hours") and order_type != "limit":
        return OrderValidationResult(False, "extended_hours_requires_limit")
    if has_qty and float(qty) % 1 != 0 and tif != "day":
        return OrderValidationResult(False, "fractional_requires_day")
    return OrderValidationResult(True)


def bracket_order_payload(
    symbol: str,
    side: str,
    qty: int,
    entry_type: str,
    time_in_force: str,
    take_profit_price: float,
    stop_loss_price: float,
    client_order_id: str,
    limit_price: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": symbol.upper(),
        "qty": str(int(qty)),
        "side": side.lower(),
        "type": entry_type,
        "time_in_force": time_in_force,
        "order_class": "bracket",
        "take_profit": {"limit_price": round(float(take_profit_price), 2)},
        "stop_loss": {"stop_price": round(float(stop_loss_price), 2)},
        "client_order_id": client_order_id,
    }
    if entry_type == "limit":
        payload["limit_price"] = round(float(limit_price or 0), 2)
    return payload


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
