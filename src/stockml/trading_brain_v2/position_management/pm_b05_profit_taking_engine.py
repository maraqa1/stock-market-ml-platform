from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import ExitAction, ExitDecision, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


class ProfitTakingEngineBlock(PlaceholderBlock):
    block_id = "PM-B05"
    name = "Profit-Taking Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=ExitAction.HOLD.value, reason="position_missing")
        decision = self.evaluate_position(position, config=payload.get("config"))
        return BrainBlockResult(block_id=self.block_id, status="ok", decision=decision.action.value, reason=decision.reason, details=decision.to_dict())

    def evaluate_position(self, position: PositionState, *, config: TradingBrainConfig | None = None) -> ExitDecision:
        cfg = config or load_trading_brain_config()
        pnl = float(position.unrealized_pl_pct)
        stage = str(position.take_profit_stage or "initial").lower()

        if self._strong_reversal_from_high(position):
            return self._decision(position, ExitAction.TRAIL, "strong_reversal_from_high_trail_remaining")
        if pnl >= cfg.trail_after_profit_pct:
            return self._decision(position, ExitAction.TRAIL, "profit_ladder_6pct_trail_remaining")
        if pnl >= cfg.take_profit_2_pct and stage not in {"second_profit_taken", "trail_remaining"}:
            return self._decision(position, ExitAction.TAKE_PROFIT, "profit_ladder_4pct_second_partial", qty_fraction=0.25)
        if pnl >= cfg.take_profit_1_pct and stage not in {"first_profit_taken", "second_profit_taken", "trail_remaining"}:
            return self._decision(position, ExitAction.TAKE_PROFIT, "profit_ladder_2pct_first_partial", qty_fraction=0.25)
        if pnl >= cfg.breakeven_move_pct and position.stop_price < position.entry_price:
            return self._decision(position, ExitAction.MOVE_STOP, "profit_ladder_1pct_move_stop_to_breakeven")
        return self._decision(position, ExitAction.HOLD, "profit_ladder_hold")

    def _strong_reversal_from_high(self, position: PositionState) -> bool:
        if position.max_favorable_excursion < 0.04:
            return False
        giveback = position.max_favorable_excursion - max(position.unrealized_pl_pct, 0.0)
        return giveback >= 0.03

    def _decision(self, position: PositionState, action: ExitAction, reason: str, *, qty_fraction: float = 0.0) -> ExitDecision:
        qty = round(abs(position.qty) * qty_fraction, 6) if qty_fraction else 0
        return ExitDecision(
            symbol=position.symbol,
            action=action,
            reason=reason,
            qty=qty,
            signal_id=position.signal_id,
            candidate_id=position.candidate_id,
            event_id=position.event_id,
            supporting_reasons=(f"pnl_pct={position.unrealized_pl_pct}", f"stage={position.take_profit_stage}"),
        )
