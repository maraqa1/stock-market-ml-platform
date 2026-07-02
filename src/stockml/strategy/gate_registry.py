from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class GateRecord:
    gate_name: str
    gate_class: str
    severity: str
    mandatory_for_new_entries: bool
    mandatory_for_current_positions: bool
    mandatory_for_overnight: bool
    position_management_trigger: bool
    tunable: str
    requires_attribution_before_tuning: bool
    default_new_entry_action: str
    default_position_action: str
    failure_meaning: str
    evidence_required_to_tune: str
    recommended_owner_module: str


def _gate(
    gate_name: str,
    gate_class: str,
    severity: str,
    *,
    mandatory_for_new_entries: bool = True,
    mandatory_for_current_positions: bool = False,
    mandatory_for_overnight: bool = False,
    position_management_trigger: bool = True,
    tunable: str = "false_without_attribution",
    requires_attribution_before_tuning: bool = True,
    default_new_entry_action: str = "block",
    default_position_action: str = "review",
    failure_meaning: str = "",
    evidence_required_to_tune: str = "blocked_vs_passed_forward_return_attribution",
    recommended_owner_module: str = "diagnostics",
) -> GateRecord:
    return GateRecord(
        gate_name=gate_name,
        gate_class=gate_class,
        severity=severity,
        mandatory_for_new_entries=mandatory_for_new_entries,
        mandatory_for_current_positions=mandatory_for_current_positions,
        mandatory_for_overnight=mandatory_for_overnight,
        position_management_trigger=position_management_trigger,
        tunable=tunable,
        requires_attribution_before_tuning=requires_attribution_before_tuning,
        default_new_entry_action=default_new_entry_action,
        default_position_action=default_position_action,
        failure_meaning=failure_meaning,
        evidence_required_to_tune=evidence_required_to_tune,
        recommended_owner_module=recommended_owner_module,
    )


GATE_REGISTRY: dict[str, GateRecord] = {
    "risk_gate_failed": _gate(
        "risk_gate_failed",
        "must_have_safety",
        "high",
        default_position_action="review",
        tunable="false_without_attribution",
        failure_meaning="aggregate risk profile is no longer acceptable",
        recommended_owner_module="stockml.trading.trade_quality_gate",
    ),
    "source_trade_action_not_executable": _gate(
        "source_trade_action_not_executable",
        "must_have_safety",
        "critical_for_new_entries",
        tunable="false",
        requires_attribution_before_tuning=False,
        default_position_action="review",
        failure_meaning="model did not explicitly approve executable trade; must not close a position by itself",
        evidence_required_to_tune="not_tunable",
        recommended_owner_module="stockml.trading.order_planner",
    ),
    "volatility_extreme": _gate(
        "volatility_extreme",
        "must_have_safety",
        "high",
        default_position_action="reduce_or_review",
        failure_meaning="candidate/position volatility is outside acceptable range",
        recommended_owner_module="stockml.trading.trade_quality_gate",
    ),
    "price_below_minimum": _gate(
        "price_below_minimum",
        "must_have_safety",
        "high",
        default_position_action="review_or_reduce",
        failure_meaning="low-price security with elevated spread/noise risk",
        recommended_owner_module="stockml.trading.trade_quality_gate",
    ),
    "market_cap_below_minimum": _gate(
        "market_cap_below_minimum",
        "must_have_safety",
        "high",
        default_position_action="review_or_reduce",
        failure_meaning="small/fragile issuer risk",
        recommended_owner_module="stockml.trading.trade_quality_gate",
    ),
    "asset_not_overnight_tradable": _gate(
        "asset_not_overnight_tradable",
        "execution_quality",
        "critical_for_overnight",
        mandatory_for_overnight=True,
        default_position_action="close_or_review_if_overnight_position",
        failure_meaning="asset should not be opened or held as normal 24/5 overnight trade",
        recommended_owner_module="stockml.trading.overnight_eligibility",
    ),
    "expected_trade_return_below_threshold": _gate(
        "expected_trade_return_below_threshold",
        "strategy_quality",
        "medium",
        default_position_action="review_or_reduce_if_losing",
        failure_meaning="expected edge no longer meets strategy threshold",
        recommended_owner_module="stockml.diagnostics.expected_return_calibration",
    ),
    "risk_adjusted_score_below_threshold": _gate(
        "risk_adjusted_score_below_threshold",
        "strategy_quality",
        "medium",
        default_position_action="review_or_reduce_if_losing",
        failure_meaning="risk-adjusted model evidence is weak",
        recommended_owner_module="stockml.trading.order_planner",
    ),
    "live_trading_disabled": _gate("live_trading_disabled", "must_have_safety", "critical", mandatory_for_current_positions=True, position_management_trigger=False, tunable="false", requires_attribution_before_tuning=False, failure_meaning="live trading must remain disabled", evidence_required_to_tune="not_tunable", recommended_owner_module="stockml.safety.live_disabled"),
    "expected_return_uncalibrated": _gate("expected_return_uncalibrated", "must_have_safety", "high", default_position_action="manual_review", failure_meaning="expected-return evidence is not execution safe", recommended_owner_module="stockml.diagnostics.expected_return_calibration"),
    "liquidity_below_minimum": _gate("liquidity_below_minimum", "execution_quality", "high", default_position_action="review_or_reduce", failure_meaning="liquidity is too thin for reliable execution", recommended_owner_module="stockml.trading.trade_quality_gate"),
    "spread_too_wide": _gate("spread_too_wide", "execution_quality", "medium", default_position_action="review", failure_meaning="spread cost may overwhelm expected edge", recommended_owner_module="stockml.trading.spread_edge"),
    "quote_stale": _gate("quote_stale", "execution_quality", "high", default_position_action="review", failure_meaning="quote is stale and execution price cannot be trusted", recommended_owner_module="stockml.trading.overnight_quote_quality"),
    "session_eligibility": _gate("session_eligibility", "execution_quality", "high", mandatory_for_overnight=True, default_position_action="review", failure_meaning="candidate is not eligible in current session", recommended_owner_module="stockml.trading.session_order_policy"),
    "overnight_tradability": _gate("overnight_tradability", "execution_quality", "critical_for_overnight", mandatory_for_overnight=True, default_position_action="review", failure_meaning="overnight trading requires asset eligibility", recommended_owner_module="stockml.trading.overnight_eligibility"),
    "anti_churn": _gate("anti_churn", "must_have_safety", "high", default_new_entry_action="block", default_position_action="manual_review", failure_meaning="prevents rapid open-close churn", recommended_owner_module="stockml.trading.anti_churn_guard"),
    "position_intent": _gate("position_intent", "must_have_safety", "high", default_position_action="manual_review", failure_meaning="prevents conflicting open/close/reverse intent", recommended_owner_module="stockml.trading.position_intent_guard"),
    "daily_auto_open_cap": _gate("daily_auto_open_cap", "must_have_safety", "medium", position_management_trigger=False, default_position_action="hold", failure_meaning="limits daily entry frequency", recommended_owner_module="stockml.autopilot.open"),
    "short_side_validation_required": _gate("short_side_validation_required", "research_only", "high", default_new_entry_action="research_only", default_position_action="review", failure_meaning="short side is not validated for execution", recommended_owner_module="stockml.candidates.short_side_policy"),
    "bottom_intraday_range_after_gap_down": _gate("bottom_intraday_range_after_gap_down", "strategy_quality", "medium", default_position_action="review_or_reduce_if_losing", failure_meaning="intraday price action is weak after a gap down", recommended_owner_module="stockml.trading.trade_quality_gate"),
    "candidate_pool_rank": _gate("candidate_pool_rank", "strategy_quality", "low", position_management_trigger=False, default_new_entry_action="rank", default_position_action="hold", failure_meaning="raw research rank is not an execution guarantee", recommended_owner_module="stockml.candidates.execution_ranker"),
    "execution_rank": _gate("execution_rank", "strategy_quality", "medium", position_management_trigger=False, default_new_entry_action="rank", default_position_action="hold", failure_meaning="execution rank after gates", recommended_owner_module="stockml.candidates.execution_ranker"),
    "missing_position_quality_evidence": _gate("missing_position_quality_evidence", "position_management_trigger", "medium", mandatory_for_new_entries=False, default_new_entry_action="block", default_position_action="manual_review", failure_meaning="position cannot be judged because current quality evidence is missing", recommended_owner_module="stockml.diagnostics.position_gate_degradation"),
    "position_currently_rejected": _gate("position_currently_rejected", "position_management_trigger", "high", mandatory_for_new_entries=False, default_new_entry_action="block", default_position_action="review_or_reduce", failure_meaning="open position fails current candidate gates", recommended_owner_module="stockml.diagnostics.position_gate_degradation"),
    "short_negative_edge": _gate("short_negative_edge", "research_only", "high", default_new_entry_action="research_only", default_position_action="review_or_reduce", failure_meaning="short validation shows negative edge", recommended_owner_module="stockml.diagnostics.short_side_performance_guard"),
    "overnight_24x5_risk": _gate("overnight_24x5_risk", "execution_quality", "high", mandatory_for_overnight=True, default_new_entry_action="diagnostics_only", default_position_action="review_or_reduce", failure_meaning="24/5 execution has special liquidity and fill risk", recommended_owner_module="stockml.trading.session_order_policy"),
}


def get_gate(gate_name: str) -> GateRecord:
    key = str(gate_name or "").strip()
    return GATE_REGISTRY.get(
        key,
        _gate(
            key or "unknown_gate",
            "experimental",
            "informational",
            mandatory_for_new_entries=False,
            position_management_trigger=False,
            tunable="unknown",
            requires_attribution_before_tuning=True,
            default_new_entry_action="diagnostic",
            default_position_action="manual_review",
            failure_meaning="unregistered gate",
            evidence_required_to_tune="register_gate_first",
            recommended_owner_module="unknown",
        ),
    )


def gates_from_reasons(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return []
    out: list[str] = []
    for raw in text.replace(";", "|").replace(",", "|").split("|"):
        gate = raw.strip()
        if gate and gate.lower() not in {"approved", "reduced"}:
            out.append(gate)
    return out


def gate_records_for(reasons: object) -> list[GateRecord]:
    return [get_gate(name) for name in gates_from_reasons(reasons)]


def registry_frame(records: Iterable[GateRecord] | None = None) -> pd.DataFrame:
    selected = list(records or GATE_REGISTRY.values())
    return pd.DataFrame([asdict(record) for record in selected]).sort_values("gate_name").reset_index(drop=True)


def write_gate_registry(path) -> pd.DataFrame:
    frame = registry_frame()
    frame.to_csv(path, index=False)
    return frame
