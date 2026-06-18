from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REVERSAL_INTENTS = {"close_long_then_reverse_short", "cover_short_then_reverse_long"}
CLOSE_OR_REDUCE_INTENTS = {
    "close_long",
    "reduce_long",
    "cover_short",
    "reduce_short",
    "close_long_then_reverse_short",
    "cover_short_then_reverse_long",
}
OPEN_OR_INCREASE_INTENTS = {"open_long", "open_short", "increase_long", "increase_short"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def normalize_order_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "long", "cover", "buy_to_cover"}:
        return "buy"
    if text in {"sell", "short", "sell_short", "sell_to_close"}:
        return "sell"
    return text


@dataclass(frozen=True)
class OrderIntent:
    intent: str
    current_qty: float
    current_side: str
    attempted_side: str
    attempted_qty: float

    @property
    def is_close_or_reduce(self) -> bool:
        return self.intent in CLOSE_OR_REDUCE_INTENTS

    @property
    def is_reversal(self) -> bool:
        return self.intent in REVERSAL_INTENTS


def position_side_from_qty(qty: Any) -> str:
    parsed = _float(qty, 0.0)
    if parsed > 0:
        return "long"
    if parsed < 0:
        return "short"
    return "none"


def derive_order_intent(*, current_qty: Any, attempted_side: Any, attempted_qty: Any) -> OrderIntent:
    qty = _float(current_qty, 0.0)
    side = position_side_from_qty(qty)
    order_side = normalize_order_side(attempted_side)
    order_qty = abs(_float(attempted_qty, 0.0))
    abs_qty = abs(qty)

    if side == "none":
        intent = "open_long" if order_side == "buy" else "open_short" if order_side == "sell" else "unknown"
    elif side == "long":
        if order_side == "buy":
            intent = "increase_long"
        elif order_side == "sell":
            if order_qty > abs_qty:
                intent = "close_long_then_reverse_short"
            elif order_qty == abs_qty:
                intent = "close_long"
            else:
                intent = "reduce_long"
        else:
            intent = "unknown"
    elif side == "short":
        if order_side == "sell":
            intent = "increase_short"
        elif order_side == "buy":
            if order_qty > abs_qty:
                intent = "cover_short_then_reverse_long"
            elif order_qty == abs_qty:
                intent = "cover_short"
            else:
                intent = "reduce_short"
        else:
            intent = "unknown"
    else:
        intent = "unknown"

    return OrderIntent(intent=intent, current_qty=qty, current_side=side, attempted_side=order_side, attempted_qty=order_qty)
