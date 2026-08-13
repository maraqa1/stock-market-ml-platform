from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.models import Candidate, EntryAction, EntryDecision, TradeIntent
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class TradeIntentBuildResult:
    symbol: str
    built: bool
    reason: str
    trade_intent: TradeIntent | None = None


class TradeIntentBuilderBlock(PlaceholderBlock):
    block_id = "AP-B11"
    name = "Trade Intent Builder"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        decision = payload.get("entry_decision")
        candidate = payload.get("candidate")
        if not isinstance(decision, EntryDecision) or not isinstance(candidate, Candidate):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="decision_or_candidate_missing")

        result = self.build_trade_intent(decision, candidate, live_price=payload.get("live_price"))
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision="TRADE_INTENT_BUILT" if result.built else "NO_TRADE_INTENT",
            reason=result.reason,
            details={"trade_intent": result.trade_intent.to_dict() if result.trade_intent else None},
        )

    def build_trade_intent(
        self,
        decision: EntryDecision,
        candidate: Candidate,
        *,
        live_price: Any,
    ) -> TradeIntentBuildResult:
        action = EntryAction(decision.action)
        if action not in {EntryAction.ENTER, EntryAction.ENTER_REDUCED}:
            return TradeIntentBuildResult(candidate.symbol, built=False, reason=f"{action.value.lower()}_does_not_create_trade_intent")

        risk_tier = str((decision.risk_profile or {}).get("risk_tier") or candidate.risk_class or "unknown")
        stop_policy, take_profit_policy, max_holding_period = self._policies(action, candidate, risk_tier)
        intent = TradeIntent(
            symbol=candidate.symbol,
            side=candidate.side,
            decision=action,
            qty=decision.qty,
            max_notional=decision.notional,
            signal_close=candidate.close_price,
            live_price_at_decision=float(live_price),
            stop_policy=stop_policy,
            take_profit_policy=take_profit_policy,
            max_holding_period=max_holding_period,
            risk_tier=risk_tier,
            warnings=candidate.warning_codes,
            signal_id=candidate.signal_id,
            candidate_id=candidate.candidate_id,
            event_id=candidate.event_id,
            source_file=candidate.source_file,
            ai2_status=candidate.ai2_status,
            warning_codes=candidate.warning_codes,
        )
        return TradeIntentBuildResult(candidate.symbol, built=True, reason="trade_intent_created", trade_intent=intent)

    def _policies(self, action: EntryAction, candidate: Candidate, risk_tier: str) -> tuple[str, str, str]:
        warnings = set(candidate.warning_codes)
        if "high_volatility" in warnings:
            return "tight_volatility_stop", "fast_profit_take", "1d"
        if candidate.ai2_status == "refresh_required":
            return "validation_first_stop", "validation_first_profit_take", "1d"
        if action is EntryAction.ENTER_REDUCED:
            return "tight_reduced_stop", "faster_profit_take", "2d"
        if risk_tier in {"normal", "high_quality"}:
            return "wider_standard_stop", "standard_profit_take", "5d"
        return "standard_stop", "standard_profit_take", "3d"
