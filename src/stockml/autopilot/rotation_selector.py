from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd


ELIGIBLE_FOR_ROTATION = {"watch_loss", "close_candidate", "stale"}
NOT_ELIGIBLE_FOR_ROTATION = {"healthy_hold", "watch_only", "watch", "fresh_or_unflagged"}
OVERRIDE_REASON_TEXT = "Significantly higher promotion score (override)"


@dataclass(frozen=True)
class RotationSelection:
    held: dict[str, Any]
    candidate: dict[str, Any]
    held_score: float
    promotion_score: float
    score_delta: float
    monitor_verdict: str = ""
    monitor_override: bool = False


def select_rotation_replacements(
    held_positions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    open_positions: list[dict[str, Any]] | None = None,
    min_score_delta: float = 0.10,
    min_hold_minutes: int = 60,
    respect_monitor_verdict: bool = True,
    monitor_override_score_delta: float = 0.20,
    now: datetime | None = None,
) -> list[RotationSelection]:
    stamp = _aware(now)
    open_rows = open_positions if open_positions is not None else held_positions
    held_symbols = {_symbol(row) for row in open_rows if _symbol(row)}
    selections: list[RotationSelection] = []
    for held in held_positions:
        if _position_open_minutes(held, stamp) < min_hold_minutes:
            continue
        if _float(held.get("unrealized_plpc")) >= 0.03:
            continue
        verdict = monitor_verdict(held)
        monitor_override = False
        threshold = min_score_delta
        if respect_monitor_verdict:
            if verdict in ELIGIBLE_FOR_ROTATION:
                threshold = min_score_delta
            elif verdict == "healthy_hold":
                threshold = max(min_score_delta, monitor_override_score_delta)
                monitor_override = True
            else:
                continue
        best = find_best_replacement(
            held,
            candidates,
            held_symbols=held_symbols,
            min_score_delta=threshold,
            monitor_verdict=verdict,
            monitor_override=monitor_override,
        )
        if best is not None:
            selections.append(best)
    return selections


def find_best_replacement(
    held: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    held_symbols: set[str] | None = None,
    min_score_delta: float = 0.10,
    monitor_verdict: str = "",
    monitor_override: bool = False,
) -> RotationSelection | None:
    held_symbol = _symbol(held)
    held_score = _position_score(held)
    held_side = _side(held.get("side") or held.get("bias"))
    excluded = {str(symbol).upper() for symbol in (held_symbols or set()) if symbol}
    eligible: list[RotationSelection] = []
    for candidate in candidates:
        candidate_symbol = _symbol(candidate)
        if not candidate_symbol or candidate_symbol == held_symbol or candidate_symbol in excluded:
            continue
        candidate_side = _side(candidate.get("nightly_bias") or candidate.get("bias") or candidate.get("side"))
        if held_side and candidate_side and held_side != candidate_side:
            continue
        promotion_score = _float(candidate.get("promotion_score"))
        delta = promotion_score - held_score
        if delta < min_score_delta:
            continue
        eligible.append(
            RotationSelection(
                held=dict(held),
                candidate=dict(candidate),
                held_score=held_score,
                promotion_score=promotion_score,
                score_delta=delta,
                monitor_verdict=monitor_verdict,
                monitor_override=monitor_override,
            )
        )
    if not eligible:
        return None
    return sorted(eligible, key=lambda item: (-item.promotion_score, _symbol(item.candidate)))[0]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        parsed = float(value)
        if pd.isna(parsed):
            return default
        return parsed
    except Exception:
        return default


def _symbol(row: dict[str, Any]) -> str:
    text = row.get("symbol")
    if text is None:
        return ""
    try:
        if pd.isna(text):
            return ""
    except Exception:
        pass
    return str(text).strip().upper()


def _side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    return text


def _position_score(position: dict[str, Any]) -> float:
    for key in ["last_promotion_score", "promotion_score", "score", "nightly_score"]:
        if key in position:
            return _float(position.get(key))
    return 0.0


def monitor_verdict(position: dict[str, Any]) -> str:
    nested = position.get("position_intelligence")
    nested = nested if isinstance(nested, dict) else {}
    values = [
        position.get("monitor_verdict"),
        position.get("position_health_status"),
        position.get("position_intelligence_management_state"),
        nested.get("management_state"),
        position.get("position_intelligence_signal_state"),
        nested.get("signal_state"),
        position.get("decision"),
    ]
    for value in values:
        verdict = _normal_verdict(value)
        if verdict:
            return verdict
    reason = _normal_verdict(position.get("decision_reason"))
    if "signal_stale" in reason or reason == "stale":
        return "stale"
    if "watch_loss" in reason:
        return "watch_loss"
    if "close_candidate" in reason or "close_triggered" in reason:
        return "close_candidate"
    if "fresh_or_unflagged" in reason:
        return "fresh_or_unflagged"
    return ""


def _normal_verdict(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"", "nan", "none"}:
        return ""
    aliases = {
        "close_triggered": "close_candidate",
        "hold": "healthy_hold",
        "healthy": "healthy_hold",
        "watch_only": "watch_only",
        "watch": "watch",
        "fresh": "fresh_or_unflagged",
        "fresh_signal": "fresh_or_unflagged",
        "latest_signal_fresh": "fresh_or_unflagged",
        "stale_signal": "stale",
        "signal_stale": "stale",
    }
    return aliases.get(text, text)


def _time(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def _position_open_minutes(row: dict[str, Any], now: datetime) -> float:
    opened = None
    for key in ["opened_at", "submitted_at", "updated_at", "filled_at"]:
        opened = _time(row.get(key))
        if opened:
            break
    if opened is None:
        return 10_000.0
    return max(0.0, (now - _aware(opened)).total_seconds() / 60)
