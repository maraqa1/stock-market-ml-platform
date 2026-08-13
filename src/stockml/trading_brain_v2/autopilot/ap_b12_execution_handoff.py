from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import AuditEvent, ExecutionFill, TradeIntent
from stockml.trading_brain_v2.shared.safety import assert_v2_live_execution_allowed
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class ExecutionHandoffResult:
    symbol: str
    mode: str
    submitted: bool
    reason: str
    audit_event: AuditEvent
    fill: ExecutionFill | None = None


class ExecutionHandoffBlock(PlaceholderBlock):
    block_id = "AP-B12"
    name = "Execution Handoff"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        intent = payload.get("trade_intent")
        if not isinstance(intent, TradeIntent):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="trade_intent_missing")

        result = self.execute_intent(
            intent,
            mode=payload.get("mode", "shadow"),
            config=payload.get("config"),
        )
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision="SUBMITTED" if result.submitted else "NOT_SUBMITTED",
            reason=result.reason,
            details={
                "audit_event": result.audit_event.to_dict(),
                "fill": result.fill.to_dict() if result.fill else None,
                "mode": result.mode,
            },
        )

    def assert_live_allowed(self, *, config: TradingBrainConfig | None = None) -> None:
        assert_v2_live_execution_allowed(requested_live_execution=True, config=config)

    def execute_intent(
        self,
        intent: TradeIntent,
        *,
        mode: str = "shadow",
        config: TradingBrainConfig | None = None,
    ) -> ExecutionHandoffResult:
        normalized_mode = str(mode or "shadow").strip().lower()
        cfg = config or load_trading_brain_config()
        if normalized_mode == "live":
            self.assert_live_allowed(config=cfg)
            audit = self._audit(intent, normalized_mode, "live_handoff_not_implemented")
            return ExecutionHandoffResult(intent.symbol, normalized_mode, submitted=False, reason="live_handoff_not_implemented", audit_event=audit)

        if normalized_mode not in {"shadow", "paper", "simulated"}:
            audit = self._audit(intent, normalized_mode, "execution_mode_unsupported")
            return ExecutionHandoffResult(intent.symbol, normalized_mode, submitted=False, reason="execution_mode_unsupported", audit_event=audit)

        fill = ExecutionFill(
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            fill_price=intent.live_price_at_decision,
            filled_at=datetime.now(timezone.utc).isoformat(),
            broker_order_id=f"sim-{intent.event_id}",
            client_order_id=f"v2-{intent.event_id}",
            signal_id=intent.signal_id,
            candidate_id=intent.candidate_id,
            event_id=intent.event_id,
        )
        audit = self._audit(intent, normalized_mode, "simulated_fill_created", fill=fill)
        return ExecutionHandoffResult(intent.symbol, normalized_mode, submitted=True, reason="simulated_fill_created", audit_event=audit, fill=fill)

    def _audit(self, intent: TradeIntent, mode: str, reason: str, *, fill: ExecutionFill | None = None) -> AuditEvent:
        return AuditEvent(
            event_at=datetime.now(timezone.utc).isoformat(),
            event_type="trading_brain_v2_execution_handoff",
            source=self.block_id,
            symbol=intent.symbol,
            message=reason,
            signal_id=intent.signal_id,
            candidate_id=intent.candidate_id,
            event_id=intent.event_id,
            details={
                "mode": mode,
                "decision": intent.decision.value,
                "qty": intent.qty,
                "max_notional": intent.max_notional,
                "fill_price": fill.fill_price if fill else None,
            },
        )
