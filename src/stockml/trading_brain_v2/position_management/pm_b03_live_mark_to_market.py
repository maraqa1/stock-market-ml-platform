from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.shared.models import PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LiveMarkToMarketBlock(PlaceholderBlock):
    block_id = "PM-B03"
    name = "Live Mark-to-Market"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="position_missing")
        marked = self.mark_to_market(position, current_price=payload.get("current_price"))
        return BrainBlockResult(block_id=self.block_id, status="ok", decision="MARKED", reason="mark_to_market_complete", details=marked.to_dict())

    def mark_to_market(self, position: PositionState, *, current_price: Any) -> PositionState:
        price = _float(current_price)
        if price is None or price <= 0:
            raise ValueError("current_price_missing_or_non_positive")
        entry = float(position.entry_price)
        qty = abs(float(position.qty))
        side = str(position.side or "").upper()
        current_value = round(qty * price, 2)
        if side == "SHORT":
            pnl = round((entry - price) * qty, 2)
            pnl_pct = (entry - price) / entry if entry else 0.0
            favorable = max(0.0, (entry - min(price, float(position.min_price_seen or entry))) / entry)
            adverse = max(0.0, (max(price, float(position.max_price_seen or entry)) - entry) / entry)
        else:
            pnl = round((price - entry) * qty, 2)
            pnl_pct = (price - entry) / entry if entry else 0.0
            favorable = max(0.0, (max(price, float(position.max_price_seen or entry)) - entry) / entry)
            adverse = max(0.0, (entry - min(price, float(position.min_price_seen or entry))) / entry)

        payload = position.to_dict()
        payload.update(
            {
                "current_price": price,
                "current_value": current_value,
                "unrealized_pl": pnl,
                "unrealized_pl_pct": round(pnl_pct, 6),
                "max_price_seen": max(price, float(position.max_price_seen or entry)),
                "min_price_seen": min(price, float(position.min_price_seen or entry)),
                "max_favorable_excursion": round(max(float(position.max_favorable_excursion or 0.0), favorable), 6),
                "max_adverse_excursion": round(max(float(position.max_adverse_excursion or 0.0), adverse), 6),
            }
        )
        return PositionState.from_dict(payload)
