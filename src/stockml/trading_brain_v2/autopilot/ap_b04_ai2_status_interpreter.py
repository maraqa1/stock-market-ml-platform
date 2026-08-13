from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.autopilot.ap_b02_candidate_normalizer import normalize_ai2_status
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class AI2StatusDecision:
    symbol: str
    ai2_status: str
    action: EntryAction
    reason: str
    eligible_for_normal_gates: bool = False


class AI2StatusInterpreterBlock(PlaceholderBlock):
    block_id = "AP-B04"
    name = "AI2 Status Interpreter"

    def evaluate(self, payload: dict | None = None) -> BrainBlockResult:
        candidates = (payload or {}).get("candidates") or []
        decisions = [self.interpret_candidate(candidate) for candidate in candidates]
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision="NO_ACTION",
            reason="ai2_status_interpretation_complete",
            details={
                "decisions": [
                    {**decision.__dict__, "action": decision.action.value}
                    for decision in decisions
                ]
            },
        )

    def interpret_status(self, status: Any, *, symbol: str = "") -> AI2StatusDecision:
        normalized = normalize_ai2_status(status)
        if normalized == "proceed":
            return AI2StatusDecision(
                symbol=symbol,
                ai2_status=normalized,
                action=EntryAction.ENTER,
                reason="ai2_proceed_continue_to_gates",
                eligible_for_normal_gates=True,
            )
        if normalized == "review":
            return AI2StatusDecision(
                symbol=symbol,
                ai2_status=normalized,
                action=EntryAction.ENTER_REDUCED,
                reason="ai2_review_requires_deterministic_reduced_refresh_or_block",
                eligible_for_normal_gates=False,
            )
        if normalized == "refresh_required":
            return AI2StatusDecision(
                symbol=symbol,
                ai2_status=normalized,
                action=EntryAction.REFRESH_AND_RECHECK,
                reason="ai2_refresh_required",
                eligible_for_normal_gates=False,
            )
        return AI2StatusDecision(
            symbol=symbol,
            ai2_status="unknown",
            action=EntryAction.BLOCK,
            reason="ai2_status_unknown",
            eligible_for_normal_gates=False,
        )

    def interpret_candidate(self, candidate: Candidate) -> AI2StatusDecision:
        return self.interpret_status(candidate.ai2_status, symbol=candidate.symbol)
