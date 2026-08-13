from __future__ import annotations

from dataclasses import dataclass

from stockml.trading_brain_v2.autopilot.ap_b02_candidate_normalizer import CandidateNormalizationResult
from stockml.trading_brain_v2.shared.models import Candidate
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


SUPPORTED_SIDES = {"LONG", "SHORT"}


@dataclass(frozen=True)
class CandidateValidityIssue:
    candidate: Candidate | None
    reasons: tuple[str, ...]
    source: dict | None = None


@dataclass(frozen=True)
class CandidateValidityResult:
    valid_candidates: list[Candidate]
    non_tradable_candidates: list[Candidate]
    invalid_candidates: list[CandidateValidityIssue]


def _normalization_reason_to_gate_reasons(reason: str) -> tuple[str, ...]:
    text = str(reason or "")
    if text.startswith("missing_required_fields:"):
        missing = [part.strip() for part in text.split(":", 1)[1].split(",") if part.strip()]
        mapped = []
        for field in missing:
            if field == "symbol":
                mapped.append("symbol_missing")
            elif field == "signal_id":
                mapped.append("signal_id_missing")
            elif field == "candidate_id":
                mapped.append("candidate_id_missing")
            elif field == "event_id":
                mapped.append("event_id_missing")
            else:
                mapped.append(f"{field}_missing")
        return tuple(mapped)
    return (text or "candidate_normalization_failed",)


class CandidateValidityGateBlock(PlaceholderBlock):
    block_id = "AP-B03"
    name = "Candidate Validity Gate"

    def evaluate(self, payload: dict | None = None) -> BrainBlockResult:
        result = self.validate_candidates((payload or {}).get("candidates") or [])
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision="NO_ACTION",
            reason="candidate_validity_complete",
            details={
                "valid_candidates": len(result.valid_candidates),
                "non_tradable_candidates": len(result.non_tradable_candidates),
                "invalid_candidates": len(result.invalid_candidates),
            },
        )

    def non_tradable_reasons(self, candidate: Candidate) -> tuple[str, ...]:
        reasons: list[str] = []
        status = str(candidate.candidate_status or "").strip().lower()
        if status == "research_only" or candidate.ai2_status == "research_only":
            reasons.append("research_only_not_tradable")
        if status == "blocked" or candidate.ai2_status == "blocked":
            reasons.append("blocked_not_tradable")
        if status and status != "executable" and not reasons:
            reasons.append("candidate_not_executable")
        return tuple(reasons)

    def validate_candidate(self, candidate: Candidate) -> tuple[str, ...]:
        reasons: list[str] = []
        executable = str(candidate.candidate_status or "").strip().lower() == "executable"
        if not candidate.symbol:
            reasons.append("symbol_missing")
        if executable and (not candidate.side or candidate.side not in SUPPORTED_SIDES):
            reasons.append("side_missing_or_unsupported")
        if candidate.close_price <= 0:
            reasons.append("latest_eod_close_missing_or_non_positive")
            reasons.append("close_price_missing_or_non_positive")
        if executable and candidate.approved_notional <= 0:
            reasons.append("approved_notional_missing_or_non_positive")
        if not candidate.latest_eod_date:
            reasons.append("latest_eod_date_missing")
        if not candidate.decision_label:
            reasons.append("execution_decision_missing")
        if not candidate.notes:
            reasons.append("notes_missing")
        if candidate.ai2_status == "unknown":
            reasons.append("ai2_status_unknown")
        if not candidate.signal_id:
            reasons.append("signal_id_missing")
        if not candidate.candidate_id:
            reasons.append("candidate_id_missing")
        if not candidate.event_id:
            reasons.append("event_id_missing")
        if executable and candidate.ai2_status == "proceed" and not candidate.price_check_clear:
            reasons.append("price_check_status_missing_or_failed")
        return tuple(reasons)

    def validate_candidates(self, candidates: list[Candidate]) -> CandidateValidityResult:
        valid: list[Candidate] = []
        non_tradable: list[Candidate] = []
        invalid: list[CandidateValidityIssue] = []
        for candidate in candidates:
            reasons = self.validate_candidate(candidate)
            if reasons:
                invalid.append(CandidateValidityIssue(candidate=candidate, reasons=reasons))
            elif self.non_tradable_reasons(candidate):
                non_tradable.append(candidate)
            else:
                valid.append(candidate)
        return CandidateValidityResult(valid_candidates=valid, non_tradable_candidates=non_tradable, invalid_candidates=invalid)

    def validate_normalization_result(self, normalization: CandidateNormalizationResult) -> CandidateValidityResult:
        result = self.validate_candidates(normalization.candidates)
        invalid = list(result.invalid_candidates)
        for issue in normalization.invalid_records:
            invalid.append(
                CandidateValidityIssue(
                    candidate=None,
                    reasons=_normalization_reason_to_gate_reasons(issue.reason),
                    source=issue.source,
                )
            )
        return CandidateValidityResult(valid_candidates=result.valid_candidates, non_tradable_candidates=result.non_tradable_candidates, invalid_candidates=invalid)
