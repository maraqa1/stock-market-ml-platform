from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProfitTakingRules:
    trailing_profit_min: float = 0.02
    trailing_giveback_threshold: float = 0.01
    fresh_signal_arm_multiplier: float = 2.0
    fresh_signal_giveback_multiplier: float = 2.0

    @classmethod
    def from_percentages(cls, arm_pct: Any = 2.0, giveback_pct: Any = 1.0) -> "ProfitTakingRules":
        return cls(
            trailing_profit_min=max(0.0, _float(arm_pct, 2.0) / 100.0),
            trailing_giveback_threshold=max(0.0, _float(giveback_pct, 1.0) / 100.0),
        )


DEFAULT_PROFIT_TAKING_RULES = ProfitTakingRules()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in [None, ""]:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def signal_state_from_reason(reason: str) -> str:
    text = str(reason or "").lower()
    if "latest_signal_unknown" in text:
        return "unknown"
    if "signal_stale" in text:
        return "stale"
    return "fresh_or_unflagged"


def classify_profit_taking(
    *,
    current_plpc: Any,
    peak_plpc: Any | None = None,
    decision_reason: str = "",
    decision: str = "",
    rules: ProfitTakingRules = DEFAULT_PROFIT_TAKING_RULES,
) -> dict[str, Any]:
    current = _float(current_plpc)
    peak = max(_float(peak_plpc, current), current)
    giveback = max(0.0, peak - current)
    signal_state = signal_state_from_reason(decision_reason)
    decision_key = str(decision or "").lower()
    weak_context = signal_state in {"stale", "unknown"} or decision_key in {"close_candidate", "replace", "rotate"}

    arm = rules.trailing_profit_min
    giveback_threshold = rules.trailing_giveback_threshold
    fresh_arm = max(arm * rules.fresh_signal_arm_multiplier, arm)
    fresh_giveback = max(giveback_threshold * rules.fresh_signal_giveback_multiplier, giveback_threshold)
    trailing_active = peak >= arm
    fresh_trailing_active = peak >= fresh_arm
    close_triggered = False
    close_reason = ""
    management_state = "hold"

    if trailing_active and weak_context and giveback >= giveback_threshold:
        close_triggered = True
        close_reason = "trailing_profit_giveback"
        management_state = "close_triggered"
    elif fresh_trailing_active and giveback >= fresh_giveback:
        close_triggered = True
        close_reason = "fresh_signal_profit_giveback"
        management_state = "close_triggered"
    elif trailing_active:
        management_state = "protect_profit"

    return {
        "signal_state": signal_state,
        "current_plpc": current,
        "peak_plpc": peak,
        "giveback_plpc": giveback,
        "trailing_active": trailing_active,
        "trailing_close_line": peak - giveback_threshold if trailing_active else None,
        "distance_to_trailing_close": current - (peak - giveback_threshold) if trailing_active else None,
        "fresh_trailing_active": fresh_trailing_active,
        "fresh_trailing_close_line": peak - fresh_giveback if fresh_trailing_active else None,
        "distance_to_fresh_trailing_close": current - (peak - fresh_giveback) if fresh_trailing_active else None,
        "close_triggered": close_triggered,
        "close_trigger_reason": close_reason,
        "management_state": management_state,
        "rule_arm": arm,
        "rule_giveback": giveback_threshold,
        "fresh_rule_arm": fresh_arm,
        "fresh_rule_giveback": fresh_giveback,
    }
