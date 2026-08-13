from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from stockml.trading_brain_v2.autopilot.ap_b08_risk_scoring_engine import RiskProfile, RiskScoringEngineBlock
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class PositionSizeDecision:
    symbol: str
    decision: str
    reason: str
    approved_notional: float
    final_notional: float
    qty: int
    portfolio_capacity_multiplier: float
    final_risk_multiplier: float


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PositionSizingEngineBlock(PlaceholderBlock):
    block_id = "AP-B09"
    name = "Position Sizing Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        candidate = payload.get("candidate")
        if not isinstance(candidate, Candidate):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="candidate_missing")

        decision = self.size_candidate(
            candidate,
            live_price=payload.get("live_price"),
            risk_profile=payload.get("risk_profile"),
            portfolio_capacity_multiplier=payload.get("portfolio_capacity_multiplier", 1.0),
        )
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision=decision.decision,
            reason=decision.reason,
            details=decision.__dict__,
        )

    def size_candidate(
        self,
        candidate: Candidate,
        *,
        live_price: Any,
        risk_profile: RiskProfile | None = None,
        portfolio_capacity_multiplier: Any = 1.0,
    ) -> PositionSizeDecision:
        live = _float(live_price)
        approved = _float(candidate.approved_notional, 0.0) or 0.0
        capacity = max(0.0, min(_float(portfolio_capacity_multiplier, 1.0) or 0.0, 1.0))
        profile = risk_profile or RiskScoringEngineBlock().score_candidate(candidate, live_price=live_price)

        if live is None or live <= 0:
            return PositionSizeDecision(candidate.symbol, EntryAction.BLOCK.value, "live_price_missing_or_non_positive", approved, 0.0, 0, capacity, profile.final_risk_multiplier)
        if approved <= 0:
            return PositionSizeDecision(candidate.symbol, EntryAction.BLOCK.value, "approved_notional_missing_or_non_positive", approved, 0.0, 0, capacity, profile.final_risk_multiplier)

        final_notional = round(
            approved
            * profile.status_multiplier
            * profile.risk_class_multiplier
            * profile.volatility_multiplier
            * profile.momentum_multiplier
            * capacity,
            2,
        )
        qty = floor(final_notional / live)
        if qty <= 0:
            return PositionSizeDecision(candidate.symbol, EntryAction.BLOCK.value, "sized_quantity_zero", approved, final_notional, qty, capacity, profile.final_risk_multiplier)

        decision = EntryAction.ENTER.value if profile.final_risk_multiplier >= 0.75 else EntryAction.ENTER_REDUCED.value
        return PositionSizeDecision(candidate.symbol, decision, "position_size_calculated", approved, final_notional, qty, capacity, profile.final_risk_multiplier)
