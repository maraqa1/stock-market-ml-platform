from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import pandas as pd
from sqlalchemy.engine import Engine

from stockml.arbitration.conflicts import log_conflict


MULTI_DAY = "multi_day_forecast"
SAME_DAY = "same_day_momentum"


def _symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").upper().strip()


def _action(row: dict[str, Any]) -> str:
    return str(row.get("trade_action") or row.get("direction") or row.get("same_day_trade_action") or "").strip().title()


def _is_actionable(action: str) -> bool:
    return action.lower() in {"long", "short"}


def _rows(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [row for row in frame.fillna("").to_dict("records") if _symbol(row)]


def _with_stream(row: dict[str, Any], stream: str, outcome: str = "emit", resolution: str = "") -> dict[str, Any]:
    payload = dict(row)
    payload["symbol"] = _symbol(payload)
    payload.setdefault("ticker", payload["symbol"])
    payload.setdefault("trade_action", _action(payload))
    payload["strategy_stream"] = stream
    payload["arbitration_outcome"] = outcome
    if resolution:
        payload["arbitration_resolution"] = resolution
    if stream == SAME_DAY:
        payload.setdefault("must_flatten_at_eod", True)
        payload.setdefault("max_hold_days", 1)
    return payload


def arbitrate_streams(
    *,
    same_day_candidates: pd.DataFrame | None,
    open_positions: pd.DataFrame | None,
    multi_day_candidate_pool: pd.DataFrame | None,
    engine: Engine | None = None,
    now: datetime | None = None,
    conflict_logger: Callable[..., None] = log_conflict,
) -> pd.DataFrame:
    same_day_by_symbol = {_symbol(row): row for row in _rows(same_day_candidates)}
    multi_day_by_symbol = {_symbol(row): row for row in _rows(multi_day_candidate_pool)}
    held_stream_by_symbol = {
        _symbol(row): str(row.get("strategy_stream") or row.get("trading_stream") or MULTI_DAY).strip().lower()
        for row in _rows(open_positions)
    }

    emitted: list[dict[str, Any]] = []
    all_symbols = sorted(set(multi_day_by_symbol) | set(same_day_by_symbol))
    for symbol in all_symbols:
        same_day = same_day_by_symbol.get(symbol)
        multi_day = multi_day_by_symbol.get(symbol)
        same_day_action = _action(same_day or {})
        multi_day_action = _action(multi_day or {})
        held_stream = held_stream_by_symbol.get(symbol, "")

        if same_day and held_stream in {"multi_day", MULTI_DAY}:
            conflict_logger(symbol, multi_day_action or None, same_day_action or None, "BLOCKED_HELD_BY_MULTI_DAY", details={"held_stream": held_stream}, engine=engine, now=now)
            continue
        if same_day and held_stream in {"same_day", SAME_DAY}:
            conflict_logger(symbol, multi_day_action or None, same_day_action or None, "BLOCKED_ALREADY_HELD_SAME_DAY", details={"held_stream": held_stream}, engine=engine, now=now)
            continue

        if same_day and multi_day and _is_actionable(multi_day_action) and _is_actionable(same_day_action):
            if multi_day_action.lower() == same_day_action.lower():
                emitted.append(_with_stream(multi_day, MULTI_DAY, resolution="MULTI_DAY_WINS_ALIGNED"))
                continue
            conflict_logger(symbol, multi_day_action, same_day_action, "CONFLICT_ABSTAIN", details={"rule": "opposite_stream_actions"}, engine=engine, now=now)
            continue

        if same_day and (not multi_day or not _is_actionable(multi_day_action)):
            emitted.append(_with_stream(same_day, SAME_DAY, resolution="SAME_DAY_FILLS_NO_DECISION" if multi_day else "SAME_DAY_ONLY"))
            continue

        if multi_day and _is_actionable(multi_day_action):
            emitted.append(_with_stream(multi_day, MULTI_DAY, resolution="MULTI_DAY_ONLY"))

    return pd.DataFrame(emitted)
