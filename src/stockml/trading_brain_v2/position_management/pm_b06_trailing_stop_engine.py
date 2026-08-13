from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import EntryAction, ExitAction, ExitDecision, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class TrailingStopResult:
    decision: ExitDecision
    position: PositionState


class TrailingStopEngineBlock(PlaceholderBlock):
    block_id = "PM-B06"
    name = "Trailing Stop Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=ExitAction.HOLD.value, reason="position_missing")
        result = self.evaluate_position(position, config=payload.get("config"))
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision=result.decision.action.value,
            reason=result.decision.reason,
            details={"decision": result.decision.to_dict(), "position": result.position.to_dict()},
        )

    def evaluate_position(self, position: PositionState, *, config: TradingBrainConfig | None = None) -> TrailingStopResult:
        updated = self.update_trailing_stop(position, config=config)
        side = str(updated.side or "").upper()
        if side == "SHORT":
            hit = updated.trailing_stop > 0 and updated.current_price >= updated.trailing_stop
        else:
            hit = updated.trailing_stop > 0 and updated.current_price <= updated.trailing_stop
        if hit:
            return TrailingStopResult(self._decision(updated, ExitAction.EXIT, "trailing_stop_hit"), updated)
        if updated.trailing_stop != position.trailing_stop:
            return TrailingStopResult(self._decision(updated, ExitAction.TRAIL, "trailing_stop_updated"), updated)
        return TrailingStopResult(self._decision(updated, ExitAction.HOLD, "trailing_stop_hold"), updated)

    def update_trailing_stop(self, position: PositionState, *, config: TradingBrainConfig | None = None) -> PositionState:
        trail_pct = self._trail_pct(position, config=config)
        side = str(position.side or "").upper()
        payload = position.to_dict()
        if side == "SHORT":
            min_seen = min(float(position.current_price), float(position.min_price_seen or position.entry_price))
            new_stop = round(min_seen * (1 + trail_pct), 4)
            payload["min_price_seen"] = min_seen
            payload["trailing_stop"] = min(float(position.trailing_stop or new_stop), new_stop)
        else:
            max_seen = max(float(position.current_price), float(position.max_price_seen or position.entry_price))
            new_stop = round(max_seen * (1 - trail_pct), 4)
            payload["max_price_seen"] = max_seen
            payload["trailing_stop"] = max(float(position.trailing_stop or 0.0), new_stop)
        return PositionState.from_dict(payload)

    def _trail_pct(self, position: PositionState, *, config: TradingBrainConfig | None = None) -> float:
        cfg = config or load_trading_brain_config()
        warnings = set(position.warnings_at_entry)
        if "high_volatility" in warnings:
            return cfg.high_volatility_trailing_stop_pct
        if EntryAction(position.entry_decision) is EntryAction.ENTER_REDUCED or position.ai2_status_at_entry == "review":
            return cfg.reduced_trailing_stop_pct
        return cfg.clean_trailing_stop_pct

    def _decision(self, position: PositionState, action: ExitAction, reason: str) -> ExitDecision:
        return ExitDecision(
            symbol=position.symbol,
            action=action,
            reason=reason,
            qty=abs(position.qty) if action is ExitAction.EXIT else 0,
            signal_id=position.signal_id,
            candidate_id=position.candidate_id,
            event_id=position.event_id,
            supporting_reasons=(f"trailing_stop={position.trailing_stop}", f"max_price_seen={position.max_price_seen}"),
        )
