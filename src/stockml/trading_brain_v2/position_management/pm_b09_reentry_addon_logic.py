from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.position_management.pm_b08_portfolio_risk_overlay import PORTFOLIO_ALLOW, PortfolioOverlayDecision
from stockml.trading_brain_v2.shared.models import PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


ADDON_ALLOW = "ALLOW_ADD"
ADDON_BLOCK = "BLOCK_ADD"


@dataclass(frozen=True)
class AddOnDecision:
    action: str
    reason: str
    add_qty: float = 0.0
    add_notional: float = 0.0


class ReEntryAddOnLogicBlock(PlaceholderBlock):
    block_id = "PM-B09"
    name = "Re-Entry / Add-On Logic"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=ADDON_BLOCK, reason="position_missing")
        decision = self.evaluate_add_on(
            position,
            refreshed_signal_status=str(payload.get("refreshed_signal_status", "")),
            portfolio_decision=payload.get("portfolio_decision"),
            requested_add_qty=float(payload.get("requested_add_qty", 0.0)),
            requested_add_notional=float(payload.get("requested_add_notional", 0.0)),
        )
        return BrainBlockResult(block_id=self.block_id, status="ok", decision=decision.action, reason=decision.reason, details=decision.__dict__)

    def evaluate_add_on(
        self,
        position: PositionState,
        *,
        refreshed_signal_status: str,
        portfolio_decision: PortfolioOverlayDecision | None = None,
        requested_add_qty: float = 0.0,
        requested_add_notional: float = 0.0,
    ) -> AddOnDecision:
        if position.unrealized_pl <= 0 or position.unrealized_pl_pct <= 0:
            return AddOnDecision(ADDON_BLOCK, "averaging_down_or_non_winner_blocked")

        if refreshed_signal_status.strip().lower() != "proceed":
            return AddOnDecision(ADDON_BLOCK, "refreshed_signal_not_proceed")

        if portfolio_decision is not None and portfolio_decision.action != PORTFOLIO_ALLOW:
            return AddOnDecision(ADDON_BLOCK, f"portfolio_overlay_{portfolio_decision.action.lower()}")

        if not self._stop_is_protected(position):
            return AddOnDecision(ADDON_BLOCK, "stop_not_at_breakeven_or_better")

        max_add_qty = abs(float(position.qty))
        add_qty = min(max(0.0, float(requested_add_qty)), max_add_qty)
        if add_qty <= 0:
            return AddOnDecision(ADDON_BLOCK, "requested_add_quantity_missing_or_zero")

        max_add_notional = abs(float(position.qty)) * float(position.entry_price)
        add_notional = min(max(0.0, float(requested_add_notional)), max_add_notional)
        if add_notional <= 0:
            add_notional = round(add_qty * float(position.current_price), 2)
        return AddOnDecision(ADDON_ALLOW, "add_on_allowed", add_qty=add_qty, add_notional=round(add_notional, 2))

    def _stop_is_protected(self, position: PositionState) -> bool:
        side = str(position.side or "").upper()
        if side == "SHORT":
            return position.stop_price <= position.entry_price
        return position.stop_price >= position.entry_price
