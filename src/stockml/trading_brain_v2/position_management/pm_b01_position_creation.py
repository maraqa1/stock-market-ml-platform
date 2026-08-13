from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.position_management.pm_b02_initial_risk_attachment import InitialRiskAttachmentBlock
from stockml.trading_brain_v2.shared.models import EntryAction, ExecutionFill, PositionState, TradeIntent
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


class PositionCreationBlock(PlaceholderBlock):
    block_id = "PM-B01"
    name = "Position Creation"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        intent = payload.get("trade_intent")
        fill = payload.get("execution_fill")
        if not isinstance(intent, TradeIntent) or not isinstance(fill, ExecutionFill):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="intent_or_fill_missing")
        position = self.create_position(intent, fill, attach_risk=bool(payload.get("attach_risk", True)))
        return BrainBlockResult(block_id=self.block_id, status="ok", decision="POSITION_CREATED", reason="position_created", details=position.to_dict())

    def create_position(self, intent: TradeIntent, fill: ExecutionFill, *, attach_risk: bool = True) -> PositionState:
        entry_price = float(fill.fill_price)
        qty = float(fill.qty)
        position = PositionState(
            symbol=fill.symbol,
            side=fill.side,
            qty=qty,
            entry_price=entry_price,
            current_price=entry_price,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
            signal_id=intent.signal_id,
            candidate_id=intent.candidate_id,
            event_id=intent.event_id,
            ai2_status_at_entry=intent.ai2_status,
            warnings_at_entry=intent.warning_codes or intent.warnings,
            risk_tier=intent.risk_tier,
            entry_decision=EntryAction(intent.decision),
            entry_reason=EntryAction(intent.decision).value.lower(),
            source_file=intent.source_file,
            entry_time=fill.filled_at,
            signal_close=float(intent.signal_close),
            max_price_seen=entry_price,
            min_price_seen=entry_price,
            max_holding_period=intent.max_holding_period,
            order_id=fill.broker_order_id,
            status="open",
            current_value=round(abs(qty) * entry_price, 2),
            trailing_stop_policy=intent.stop_policy,
            take_profit_policy=intent.take_profit_policy,
        )
        if not attach_risk:
            return position
        return InitialRiskAttachmentBlock().attach_initial_risk(position, intent)
