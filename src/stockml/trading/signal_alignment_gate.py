from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SignalAlignmentDecision:
    allowed: bool
    reason: str = ""


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _side(value: Any) -> str:
    text = _text(value).lower()
    if text in {"buy", "long"} or "long" in text:
        return "long"
    if text in {"sell", "short"} or "short" in text:
        return "short"
    return ""


def _first(row: dict[str, Any], details: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = details.get(key, row.get(key))
        if _text(value):
            return value
    return ""


def evaluate_entry_signal_alignment(candidate: dict[str, Any], details: dict[str, Any] | None = None) -> SignalAlignmentDecision:
    details = details or {}
    status = _text(_first(candidate, details, ("latest_signal_status", "signal_state", "latest_signal"))).lower()
    if status in {"unknown", "latest_signal_unknown"}:
        return SignalAlignmentDecision(False, "latest_signal_unknown_blocks_entry")
    if status in {"stale", "signal_stale"}:
        return SignalAlignmentDecision(False, "stale_signal_blocks_entry")

    model_status = _text(_first(candidate, details, ("model_status",)))
    decision_grade = _text(_first(candidate, details, ("decision_grade",)))
    if model_status and model_status != "decision_grade":
        return SignalAlignmentDecision(False, "model_not_decision_grade")
    if decision_grade and decision_grade != "decision_grade":
        return SignalAlignmentDecision(False, "model_not_decision_grade")

    trade_side = _side(_first(candidate, details, ("side", "current_trade_action", "trade_action", "nightly_bias")))
    signal_side = _side(_first(candidate, details, ("latest_signal_direction", "signal_direction", "direction_context")))
    if trade_side and signal_side and trade_side != signal_side:
        return SignalAlignmentDecision(False, "signal_direction_mismatch")

    return SignalAlignmentDecision(True, "")
