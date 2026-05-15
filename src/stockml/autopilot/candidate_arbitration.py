from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


SOURCE_PRIORITIES = {
    "strong_promotion": 40.0,
    "per_symbol_forecast": 30.0,
    "near_miss": 20.0,
    "flat_account_fallback": 10.0,
}

HARD_BLOCK_REASONS = {
    "blocked_kill_switch",
    "kill_switch_active",
    "blocked_stale_quote",
    "stale_quote",
    "blocked_wide_spread",
    "wide_spread",
    "rejected_price_min",
    "price_below_minimum",
    "rejected_liquidity_thin",
    "liquidity_below_minimum",
    "hard_fail",
}


@dataclass(frozen=True)
class ArbitrationResult:
    candidate: dict[str, Any]
    score: float
    source: str
    components: dict[str, float]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _details(candidate: dict[str, Any]) -> dict[str, Any]:
    details = candidate.get("details")
    return details if isinstance(details, dict) else {}


def candidate_source(candidate: dict[str, Any], fallback_source: str = "") -> str:
    details = _details(candidate)
    if details.get("candidate_source"):
        return str(details.get("candidate_source"))
    if details.get("per_symbol_forecast_fallback"):
        return "per_symbol_forecast"
    if details.get("near_miss_fallback"):
        return "near_miss"
    if details.get("flat_account_fallback"):
        return "flat_account_fallback"
    return fallback_source or "strong_promotion"


def hard_block_reason(candidate: dict[str, Any], held_symbols: set[str] | None = None) -> str:
    symbol = str(candidate.get("symbol") or "").upper()
    if not symbol:
        return "missing_symbol"
    if symbol in (held_symbols or set()) or bool(candidate.get("is_held")):
        return "already_held"
    details = _details(candidate)
    severity = str(details.get("severity") or "").strip().lower()
    if severity == "hard_fail":
        return "hard_fail"
    for key in ("block_reason", "outcome_reason", "failed_gate", "arbitration_block_reason"):
        value = str(details.get(key) or candidate.get(key) or "").strip().lower()
        if value in HARD_BLOCK_REASONS:
            return value
    return ""


def arbitration_components(candidate: dict[str, Any], source: str | None = None) -> dict[str, float]:
    source_name = source or candidate_source(candidate)
    details = _details(candidate)
    source_priority = SOURCE_PRIORITIES.get(source_name, 0.0)
    promotion = _float(candidate.get("promotion_score"))

    if source_name == "per_symbol_forecast":
        profitability = _float(details.get("expected_profitability_score"), promotion)
        confirmation = _float(details.get("confirmation_score"))
        move = min(max(_float(details.get("expected_move_bps")), 0.0), 500.0)
        return {
            "source_priority": source_priority,
            "profitability": profitability,
            "confirmation": confirmation * 0.25,
            "expected_move": move * 0.10,
            "promotion": promotion,
        }

    if source_name == "near_miss":
        distance_pct = min(max(_float(details.get("distance_pct"), 1.0), 0.0), 1.0)
        severity = str(details.get("severity") or "").lower()
        severity_bonus = 10.0 if severity == "near_miss" else (0.0 if severity == "moderate_gap" else -25.0)
        return {
            "source_priority": source_priority,
            "distance": (1.0 - distance_pct) * 100.0,
            "severity": severity_bonus,
            "promotion": promotion * 10.0,
        }

    return {
        "source_priority": source_priority,
        "promotion": promotion,
    }


def arbitration_score(candidate: dict[str, Any], source: str | None = None) -> float:
    return sum(arbitration_components(candidate, source).values())


def _copy_with_arbitration(candidate: dict[str, Any], source: str, score: float, components: dict[str, float]) -> dict[str, Any]:
    payload = dict(candidate)
    payload["symbol"] = str(payload.get("symbol") or "").upper()
    details = dict(_details(payload))
    if source == "per_symbol_forecast":
        details.setdefault("per_symbol_forecast_fallback", True)
        details.setdefault("fallback_reason", "per_symbol_forecast_confirmed_candidate")
    elif source == "near_miss":
        details.setdefault("near_miss_fallback", True)
        details.setdefault("fallback_reason", "near_miss_diagnostic_candidate")
    elif source == "flat_account_fallback":
        details.setdefault("flat_account_fallback", True)
        details.setdefault("fallback_reason", "flat_account_no_strong_promotions")
    details["candidate_source"] = source
    details["candidate_strength_score"] = score
    details["arbitration_score"] = score
    details["arbitration_components"] = components
    details["arbitration_status"] = "selected"
    payload["details"] = details
    payload["candidate_strength_score"] = score
    return payload


def arbitrate_candidates(
    candidate_groups: Iterable[tuple[str, Iterable[dict[str, Any]]]],
    *,
    held_symbols: set[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected: dict[str, ArbitrationResult] = {}
    held = {str(symbol).upper() for symbol in (held_symbols or set()) if str(symbol).strip()}
    for source, candidates in candidate_groups:
        for candidate in candidates:
            block_reason = hard_block_reason(candidate, held)
            if block_reason:
                continue
            source_name = candidate_source(candidate, source)
            components = arbitration_components(candidate, source_name)
            score = sum(components.values())
            payload = _copy_with_arbitration(candidate, source_name, score, components)
            symbol = payload["symbol"]
            previous = selected.get(symbol)
            if previous is None or score > previous.score:
                selected[symbol] = ArbitrationResult(payload, score, source_name, components)
    ranked = sorted(
        (result.candidate for result in selected.values()),
        key=lambda row: (
            -_float(row.get("candidate_strength_score")),
            -SOURCE_PRIORITIES.get(candidate_source(row), 0.0),
            str(row.get("symbol") or ""),
        ),
    )
    return ranked[:limit] if limit is not None else ranked
