from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.shared.models import ExitAction, ExitDecision, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


class StopLossEngineBlock(PlaceholderBlock):
    block_id = "PM-B04"
    name = "Stop-Loss Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=ExitAction.EXIT.value, reason="position_missing")
        decision = self.evaluate_position(
            position,
            max_position_loss_pct=float(payload.get("max_position_loss_pct", 0.03)),
            portfolio_forced_exit=bool(payload.get("portfolio_forced_exit", False)),
        )
        return BrainBlockResult(block_id=self.block_id, status="ok", decision=decision.action.value, reason=decision.reason, details=decision.to_dict())

    def evaluate_position(
        self,
        position: PositionState,
        *,
        max_position_loss_pct: float = 0.03,
        portfolio_forced_exit: bool = False,
    ) -> ExitDecision:
        if portfolio_forced_exit:
            return self._decision(position, ExitAction.EXIT, "portfolio_forced_exit")
        if position.qty == 0 or position.entry_price <= 0 or position.current_price <= 0:
            return self._decision(position, ExitAction.EXIT, "invalid_entry_state")

        side = str(position.side or "").upper()
        if side == "SHORT":
            stop_hit = position.stop_price > 0 and position.current_price >= position.stop_price
        else:
            stop_hit = position.stop_price > 0 and position.current_price <= position.stop_price
        if stop_hit:
            return self._decision(position, ExitAction.EXIT, "stop_loss_hit")

        if position.unrealized_pl_pct <= -abs(max_position_loss_pct):
            return self._decision(position, ExitAction.EXIT, "max_position_loss_exceeded")

        return self._decision(position, ExitAction.HOLD, "stop_loss_not_triggered")

    def _decision(self, position: PositionState, action: ExitAction, reason: str) -> ExitDecision:
        return ExitDecision(
            symbol=position.symbol,
            action=action,
            reason=reason,
            qty=abs(position.qty) if action is ExitAction.EXIT else 0,
            signal_id=position.signal_id,
            candidate_id=position.candidate_id,
            event_id=position.event_id,
            supporting_reasons=(f"current_price={position.current_price}", f"pnl_pct={position.unrealized_pl_pct}"),
        )
