from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.autopilot.ap_b03_candidate_validity_gate import CandidateValidityGateBlock
from stockml.trading_brain_v2.autopilot.ap_b04_ai2_status_interpreter import AI2StatusDecision, AI2StatusInterpreterBlock
from stockml.trading_brain_v2.autopilot.ap_b05_warning_interpreter import WarningInterpretation, WarningInterpreterBlock
from stockml.trading_brain_v2.autopilot.ap_b06_refresh_gate import RefreshGateBlock, RefreshGateDecision
from stockml.trading_brain_v2.autopilot.ap_b07_tradability_gate import TradabilityGateBlock, TradabilityGateDecision
from stockml.trading_brain_v2.autopilot.ap_b08_risk_scoring_engine import RiskProfile, RiskScoringEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b09_position_sizing_engine import PositionSizeDecision, PositionSizingEngineBlock
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction, EntryDecision
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


class EntryDecisionEngineBlock(PlaceholderBlock):
    block_id = "AP-B10"
    name = "Entry Decision Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        candidate = payload.get("candidate")
        if not isinstance(candidate, Candidate):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=EntryAction.BLOCK.value, reason="candidate_missing")

        decision = self.decide(
            candidate,
            live_price=payload.get("live_price"),
            market_snapshot=payload.get("market_snapshot"),
            expected_latest_eod_date=payload.get("expected_latest_eod_date"),
            validity_reasons=payload.get("validity_reasons"),
            ai2_decision=payload.get("ai2_decision"),
            warning_decision=payload.get("warning_decision"),
            refresh_decision=payload.get("refresh_decision"),
            tradability_decision=payload.get("tradability_decision"),
            risk_profile=payload.get("risk_profile"),
            size_decision=payload.get("size_decision"),
        )
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision=decision.action.value,
            reason=decision.reason,
            details=decision.to_dict(),
        )

    def decide(
        self,
        candidate: Candidate,
        *,
        live_price: Any = None,
        market_snapshot: dict[str, Any] | None = None,
        expected_latest_eod_date: Any = None,
        validity_reasons: tuple[str, ...] | list[str] | None = None,
        ai2_decision: AI2StatusDecision | None = None,
        warning_decision: WarningInterpretation | None = None,
        refresh_decision: RefreshGateDecision | None = None,
        tradability_decision: TradabilityGateDecision | None = None,
        risk_profile: RiskProfile | None = None,
        size_decision: PositionSizeDecision | None = None,
    ) -> EntryDecision:
        reasons: list[str] = []
        invalid_reasons = tuple(validity_reasons) if validity_reasons is not None else CandidateValidityGateBlock().validate_candidate(candidate)
        if invalid_reasons:
            return self._decision(candidate, EntryAction.BLOCK, "invalid_candidate", reasons=invalid_reasons)

        ai2 = ai2_decision or AI2StatusInterpreterBlock().interpret_candidate(candidate)
        warning = warning_decision or WarningInterpreterBlock().interpret_candidate(candidate)
        refresh = refresh_decision or RefreshGateBlock().evaluate_candidate(candidate, live_price=live_price, expected_latest_eod_date=expected_latest_eod_date)
        snapshot = dict(market_snapshot or {})
        if live_price is not None and "live_price" not in snapshot:
            snapshot["live_price"] = live_price
        tradability = tradability_decision or TradabilityGateBlock().evaluate_candidate(candidate, market_snapshot=snapshot)
        risk = risk_profile or RiskScoringEngineBlock().score_candidate(candidate, live_price=live_price)
        size = size_decision or PositionSizingEngineBlock().size_candidate(candidate, live_price=live_price, risk_profile=risk)

        if warning.reason == "price_check_failed" or "price_check_failed" in warning.warning_codes:
            return self._decision(candidate, EntryAction.BLOCK, "price_check_failed", risk=risk, size=size, reasons=warning.warning_codes)
        if ai2.action is EntryAction.REFRESH_AND_RECHECK:
            return self._decision(candidate, EntryAction.REFRESH_AND_RECHECK, ai2.reason, risk=risk, size=size, reasons=(ai2.reason,))
        if refresh.decision == EntryAction.REFRESH_AND_RECHECK.value:
            return self._decision(candidate, EntryAction.REFRESH_AND_RECHECK, refresh.reason, risk=risk, size=size, reasons=(refresh.reason,))
        if refresh.decision == EntryAction.BLOCK.value:
            return self._decision(candidate, EntryAction.BLOCK, refresh.reason, risk=risk, size=size, reasons=(refresh.reason,))
        if tradability.decision == EntryAction.BLOCK.value:
            return self._decision(candidate, EntryAction.BLOCK, tradability.reason, risk=risk, size=size, reasons=(tradability.reason,))
        if ai2.action is EntryAction.BLOCK:
            return self._decision(candidate, EntryAction.BLOCK, ai2.reason, risk=risk, size=size, reasons=(ai2.reason,))
        if risk.final_risk_multiplier <= 0:
            return self._decision(candidate, EntryAction.BLOCK, "risk_multiplier_zero", risk=risk, size=size, reasons=risk.reasons)
        if size.qty <= 0:
            return self._decision(candidate, EntryAction.BLOCK, size.reason, risk=risk, size=size, reasons=(size.reason,))

        reasons.extend([ai2.reason, warning.reason, refresh.reason, tradability.reason, size.reason])
        if ai2.action is EntryAction.ENTER_REDUCED or warning.action is EntryAction.ENTER_REDUCED or size.decision == EntryAction.ENTER_REDUCED.value:
            return self._decision(candidate, EntryAction.ENTER_REDUCED, "entry_reduced", risk=risk, size=size, reasons=tuple(reasons))

        return self._decision(candidate, EntryAction.ENTER, "entry_approved", risk=risk, size=size, reasons=tuple(reasons))

    def _decision(
        self,
        candidate: Candidate,
        action: EntryAction,
        reason: str,
        *,
        risk: RiskProfile | None = None,
        size: PositionSizeDecision | None = None,
        reasons: tuple[str, ...] | list[str] = (),
    ) -> EntryDecision:
        return EntryDecision(
            symbol=candidate.symbol,
            action=action,
            reason=reason,
            candidate_id=candidate.candidate_id,
            signal_id=candidate.signal_id,
            event_id=candidate.event_id,
            qty=size.qty if size else 0,
            notional=size.final_notional if size else 0.0,
            risk_profile=risk.__dict__ if risk else {},
            warnings=candidate.warning_codes,
            supporting_reasons=tuple(reasons),
            source_file=candidate.source_file,
        )
