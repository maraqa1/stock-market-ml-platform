from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.position_management.pm_b04_stop_loss_engine import StopLossEngineBlock
from stockml.trading_brain_v2.position_management.pm_b05_profit_taking_engine import ProfitTakingEngineBlock
from stockml.trading_brain_v2.position_management.pm_b06_trailing_stop_engine import TrailingStopEngineBlock
from stockml.trading_brain_v2.position_management.pm_b07_time_based_exit_engine import TimeBasedExitEngineBlock
from stockml.trading_brain_v2.position_management.pm_b08_portfolio_risk_overlay import PORTFOLIO_FORCE_EXIT_INVALID, PORTFOLIO_REDUCE_RISK, PortfolioOverlayDecision
from stockml.trading_brain_v2.shared.models import ExitAction, ExitDecision, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


PRIORITY = {
    ExitAction.EXIT: 5,
    ExitAction.TAKE_PROFIT: 4,
    ExitAction.SCALE_DOWN: 3,
    ExitAction.TRAIL: 2,
    ExitAction.MOVE_STOP: 1,
    ExitAction.HOLD: 0,
}


class ExitDecisionEngineBlock(PlaceholderBlock):
    block_id = "PM-B10"
    name = "Exit Decision Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=ExitAction.EXIT.value, reason="position_missing")
        decision = self.decide(
            position,
            portfolio_decision=payload.get("portfolio_decision"),
            current_time=payload.get("current_time"),
        )
        return BrainBlockResult(block_id=self.block_id, status="ok", decision=decision.action.value, reason=decision.reason, details=decision.to_dict())

    def decide(
        self,
        position: PositionState,
        *,
        portfolio_decision: PortfolioOverlayDecision | None = None,
        current_time: Any = None,
        stop_decision: ExitDecision | None = None,
        profit_decision: ExitDecision | None = None,
        trailing_decision: ExitDecision | None = None,
        time_decision: ExitDecision | None = None,
    ) -> ExitDecision:
        if portfolio_decision and portfolio_decision.action in {PORTFOLIO_FORCE_EXIT_INVALID, PORTFOLIO_REDUCE_RISK}:
            return self._decorate(position, ExitDecision(position.symbol, ExitAction.EXIT, portfolio_decision.reason, abs(position.qty), position.signal_id, position.candidate_id, position.event_id))

        decisions = [
            stop_decision or StopLossEngineBlock().evaluate_position(position),
            profit_decision or ProfitTakingEngineBlock().evaluate_position(position),
            (trailing_decision or TrailingStopEngineBlock().evaluate_position(position).decision),
            time_decision or TimeBasedExitEngineBlock().evaluate_position(position, current_time=current_time),
        ]
        selected = max(decisions, key=lambda decision: PRIORITY[ExitAction(decision.action)])
        return self._decorate(position, selected)

    def _decorate(self, position: PositionState, decision: ExitDecision) -> ExitDecision:
        payload = decision.to_dict()
        payload.update(
            {
                "current_price": position.current_price,
                "pnl": position.unrealized_pl,
                "pnl_pct": position.unrealized_pl_pct,
            }
        )
        return ExitDecision.from_dict(payload)
