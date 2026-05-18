from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PositionHealthRules:
    max_position_loss_pct: float = 2.0
    hard_stop_loss_pct: float = 4.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except Exception:
        return default


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _signal_state(position: dict[str, Any]) -> str:
    state = _text(position.get("latest_signal_status") or position.get("signal_state") or position.get("position_intelligence_signal_state")).lower()
    reason = _text(position.get("decision_reason") or position.get("position_intelligence_decision_reason")).lower()
    if state in {"unknown", "stale", "fresh"}:
        return state
    if "latest_signal_unknown" in reason:
        return "unknown"
    if "signal_stale" in reason:
        return "stale"
    return state or "fresh"


def _reversal_confirmed(position: dict[str, Any]) -> bool:
    value = position.get("signal_reversal_confirmed") or position.get("reversal_confirmed")
    if isinstance(value, bool):
        return value
    if _text(value).lower() in {"true", "1", "yes"}:
        return True
    reason = _text(position.get("decision_reason") or position.get("position_intelligence_decision_reason")).lower()
    return "signal_reversal_confirmed" in reason or "signal_flip_confirmed" in reason


def classify_position_health(position: dict[str, Any], rules: PositionHealthRules | None = None) -> dict[str, Any]:
    cfg = rules or PositionHealthRules()
    plpc = _float(position.get("unrealized_plpc") or position.get("position_unrealized_plpc"))
    signal_state = _signal_state(position)
    risk_tier = _text(position.get("risk_tier") or position.get("risk_verdict")).lower()
    hard_stop = -abs(cfg.hard_stop_loss_pct) / 100.0
    loss_threshold = -abs(cfg.max_position_loss_pct) / 100.0

    if plpc <= hard_stop:
        status, reason = "close_now", "hard_stop_hit"
    elif _reversal_confirmed(position):
        status, reason = "close_now", "signal_reversal_confirmed"
    elif risk_tier in {"reject", "rejected", "hard_reject", "block"}:
        status, reason = "close_candidate", "risk_tier_reject"
    elif signal_state in {"stale", "unknown"} and plpc <= loss_threshold:
        reason_state = "signal_stale" if signal_state == "stale" else "latest_signal_unknown"
        status, reason = "close_candidate", f"loss_threshold_breached;{reason_state}"
    elif plpc <= loss_threshold:
        status, reason = "close_candidate", "loss_threshold_breached"
    elif signal_state in {"stale", "unknown"} and plpc < 0:
        reason_state = "signal_stale" if signal_state == "stale" else "latest_signal_unknown"
        status, reason = "watch_loss", f"small_red_above_stop;{reason_state}"
    elif signal_state == "unknown" and plpc > 0:
        status, reason = "watch", "latest_signal_unknown_green_position"
    elif signal_state == "unknown":
        status, reason = "manual_review", "latest_signal_unknown"
    elif plpc < 0:
        status, reason = "watch_loss", "small_red_above_stop"
    elif plpc > 0:
        status, reason = "healthy_hold", "green_position_no_risk_issue"
    else:
        status, reason = "watch", "flat_position"

    return {
        "position_health_status": status,
        "position_health_reason": reason,
        "latest_signal_status": signal_state,
    }
