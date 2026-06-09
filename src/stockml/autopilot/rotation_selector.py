from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RotationSelection:
    held: dict[str, Any]
    candidate: dict[str, Any]
    held_score: float
    promotion_score: float
    score_delta: float


def select_rotation_replacements(
    held_positions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    open_positions: list[dict[str, Any]] | None = None,
    min_score_delta: float = 0.10,
    min_hold_minutes: int = 60,
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
        best = find_best_replacement(
            held,
            candidates,
            held_symbols=held_symbols,
            min_score_delta=min_score_delta,
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
