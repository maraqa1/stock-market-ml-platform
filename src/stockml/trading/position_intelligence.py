from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from stockml.trading.profit_taking import DEFAULT_PROFIT_TAKING_RULES, ProfitTakingRules, classify_profit_taking


@dataclass(frozen=True)
class PositionRiskRules:
    hard_stop_loss_threshold: float = -0.04
    defensive_stale_loss_threshold: float = -0.025
    defensive_unknown_loss_threshold: float = -0.02
    profit_taking_rules: ProfitTakingRules = DEFAULT_PROFIT_TAKING_RULES


DEFAULT_RULES = PositionRiskRules()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in [None, ""]:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _decision_by_symbol(decisions: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in decisions or []:
        symbol = _symbol(row.get("symbol"))
        if symbol:
            out[symbol] = dict(row)
    return out


def _signal_state(reason: str) -> str:
    text = reason.lower()
    if "latest_signal_unknown" in text:
        return "unknown"
    if "signal_stale" in text:
        return "stale"
    return "fresh_or_unflagged"


def _defensive_line(signal_state: str, rules: PositionRiskRules) -> float | None:
    if signal_state == "unknown":
        return rules.defensive_unknown_loss_threshold
    if signal_state == "stale":
        return rules.defensive_stale_loss_threshold
    return None


def explain_position(
    position: dict[str, Any],
    *,
    decision: dict[str, Any] | None = None,
    peak_plpc: float | None = None,
    rules: PositionRiskRules = DEFAULT_RULES,
) -> dict[str, Any]:
    symbol = _symbol(position.get("symbol"))
    current_plpc = _float(position.get("unrealized_plpc"))
    peak = max(_float(peak_plpc, current_plpc), current_plpc)
    giveback = max(0.0, peak - current_plpc)
    reason = str((decision or {}).get("decision_reason") or position.get("decision_reason") or "")
    decision_value = str((decision or {}).get("decision") or position.get("decision") or "").lower()
    signal_state = _signal_state(reason)
    profit = classify_profit_taking(
        current_plpc=current_plpc,
        peak_plpc=peak,
        decision_reason=reason,
        decision=decision_value,
        rules=rules.profit_taking_rules,
    )
    trailing_triggered = bool(profit["close_triggered"])
    defensive_line = _defensive_line(signal_state, rules)
    defensive_distance = current_plpc - defensive_line if defensive_line is not None else None
    defensive_triggered = defensive_distance is not None and defensive_distance <= 0
    hard_stop_distance = current_plpc - rules.hard_stop_loss_threshold
    hard_stop_triggered = hard_stop_distance <= 0

    if hard_stop_triggered:
        state = "close_triggered"
        summary = "Hard stop loss is breached."
    elif trailing_triggered:
        state = "close_triggered"
        summary = "Profit-protection giveback is breached."
    elif defensive_triggered:
        state = "close_triggered"
        summary = f"Defensive {signal_state} signal loss threshold is breached."
    elif profit["trailing_active"]:
        state = "protect_profit"
        summary = "Trailing profit protection is armed."
    elif current_plpc < 0:
        state = "watch_loss"
        summary = "Position is losing but remains above defensive close thresholds."
    elif decision_value == "watch":
        state = "watch"
        summary = "Monitor recommends watch; no close trigger is breached."
    else:
        state = "hold"
        summary = "Position remains inside management rules."

    return {
        "symbol": symbol,
        "management_state": state,
        "management_summary": summary,
        "signal_state": signal_state,
        "decision": decision_value,
        "decision_reason": reason,
        "current_plpc": current_plpc,
        "peak_plpc": peak,
        "giveback_plpc": giveback,
        "trailing_active": profit["trailing_active"],
        "trailing_close_line": profit["trailing_close_line"],
        "distance_to_trailing_close": profit["distance_to_trailing_close"],
        "fresh_trailing_active": profit["fresh_trailing_active"],
        "fresh_trailing_close_line": profit["fresh_trailing_close_line"],
        "distance_to_fresh_trailing_close": profit["distance_to_fresh_trailing_close"],
        "profit_rule_arm": profit["rule_arm"],
        "profit_rule_giveback": profit["rule_giveback"],
        "fresh_profit_rule_arm": profit["fresh_rule_arm"],
        "fresh_profit_rule_giveback": profit["fresh_rule_giveback"],
        "defensive_close_line": defensive_line,
        "distance_to_defensive_close": defensive_distance,
        "hard_stop_line": rules.hard_stop_loss_threshold,
        "distance_to_hard_stop": hard_stop_distance,
        "close_triggered": hard_stop_triggered or trailing_triggered or defensive_triggered,
        "close_trigger_reason": (
            "hard_stop_loss"
            if hard_stop_triggered
            else profit["close_trigger_reason"]
            if trailing_triggered
            else f"defensive_{signal_state}_loss"
            if defensive_triggered
            else ""
        ),
    }


def enrich_positions(
    positions: Iterable[dict[str, Any]],
    *,
    decisions: Iterable[dict[str, Any]] | None = None,
    autopilot_state: dict[str, Any] | None = None,
    rules: PositionRiskRules = DEFAULT_RULES,
) -> list[dict[str, Any]]:
    decision_map = _decision_by_symbol(decisions)
    peaks = (autopilot_state or {}).get("position_peak_plpc") if isinstance((autopilot_state or {}).get("position_peak_plpc"), dict) else {}
    enriched: list[dict[str, Any]] = []
    for row in positions:
        item = dict(row)
        symbol = _symbol(item.get("symbol"))
        intelligence = explain_position(
            item,
            decision=decision_map.get(symbol),
            peak_plpc=_float((peaks or {}).get(symbol), _float(item.get("unrealized_plpc"))),
            rules=rules,
        )
        item.update({f"position_intelligence_{key}": value for key, value in intelligence.items() if key != "symbol"})
        item["position_intelligence"] = intelligence
        enriched.append(item)
    return enriched
