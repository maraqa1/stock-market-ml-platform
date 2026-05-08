from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ExecutionRiskPolicy:
    max_portfolio_exposure: float = 10_000.0
    max_single_position_exposure: float = 1_000.0
    max_order_notional: float = 1_000.0
    max_daily_trades: int = 10
    max_daily_loss: float = 500.0
    min_confidence: float = 0.55
    min_avg_dollar_volume: float = 5_000_000.0
    allow_short_selling: bool = False
    allow_market_closed_trading: bool = False
    max_sector_exposure_fraction: float = 0.40


def _num(value: object, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(parsed) else parsed)


class RiskManager:
    def __init__(
        self,
        policy: ExecutionRiskPolicy | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | None = None,
        account: dict[str, Any] | None = None,
        market_open: bool = True,
    ) -> None:
        self.policy = policy or ExecutionRiskPolicy()
        self.open_orders = open_orders or []
        self.positions = positions or []
        self.account = account or {}
        self.market_open = market_open

    def validate_recommendation(self, rec: dict[str, Any], daily_count: int = 0) -> tuple[bool, str]:
        symbol = str(rec.get("symbol") or rec.get("ticker") or "").upper()
        signal = str(rec.get("signal") or rec.get("trade_action") or "").lower()
        confidence = _num(rec.get("confidence", rec.get("confidence_score", rec.get("side_probability"))))
        avg_dollar_volume = _num(rec.get("avg_dollar_volume", rec.get("avg_dollar_volume_20d")))
        notional = min(_num(rec.get("recommended_notional"), self.policy.max_order_notional), self.policy.max_order_notional)
        sector = str(rec.get("sector") or "Unknown")
        if not symbol:
            return False, "missing_symbol"
        if signal in {"hold", "no decision", "neutral"}:
            return False, "hold_or_no_decision"
        if signal == "short" and not self.policy.allow_short_selling:
            return False, "shorting_disabled"
        if confidence < self.policy.min_confidence:
            return False, "confidence_below_threshold"
        if avg_dollar_volume < self.policy.min_avg_dollar_volume:
            return False, "liquidity_below_minimum"
        if daily_count >= self.policy.max_daily_trades:
            return False, "max_daily_trades_reached"
        if not self.market_open and not self.policy.allow_market_closed_trading:
            return False, "market_closed"
        if self._has_duplicate_order(symbol, "buy" if signal == "long" else "sell"):
            return False, "duplicate_open_order"
        if notional > self.remaining_position_capacity(symbol):
            return False, "max_position_exposure_reached"
        if self.sector_exposure(sector) + notional > self.policy.max_portfolio_exposure * self.policy.max_sector_exposure_fraction:
            return False, "sector_exposure_limit"
        return True, "risk_passed"

    def approved_notional(self, rec: dict[str, Any]) -> float:
        symbol = str(rec.get("symbol") or rec.get("ticker") or "").upper()
        requested = _num(rec.get("recommended_notional"), self.policy.max_order_notional)
        return round(max(0.0, min(requested, self.policy.max_order_notional, self.remaining_position_capacity(symbol))), 2)

    def remaining_position_capacity(self, symbol: str) -> float:
        current = 0.0
        for pos in self.positions:
            if str(pos.get("symbol") or "").upper() == symbol.upper():
                current += abs(_num(pos.get("market_value"), _num(pos.get("notional"))))
        return max(0.0, self.policy.max_single_position_exposure - current)

    def sector_exposure(self, sector: str) -> float:
        total = 0.0
        for pos in self.positions:
            if str(pos.get("sector") or "Unknown") == sector:
                total += abs(_num(pos.get("market_value"), _num(pos.get("notional"))))
        return total

    def _has_duplicate_order(self, symbol: str, side: str) -> bool:
        return any(str(order.get("symbol") or "").upper() == symbol.upper() and str(order.get("side") or "").lower() == side for order in self.open_orders)
