from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.trading.manual_position_actions import apply_manual_position_action
from stockml.trading.monitor_auto_close import execute_monitor_auto_closes
from stockml.trading.paper_autopilot import (
    append_tick_log,
    apply_paper_autopilot_decisions,
    load_state,
    save_state,
    update_position_peaks,
)


def _clean_symbols(symbols: set[str] | None) -> set[str]:
    return {str(symbol).strip().upper() for symbol in (symbols or set()) if str(symbol).strip()}


def _paper_autopilot_active(state: dict[str, Any]) -> bool:
    return str(state.get("status") or "").lower() == "running" and str(state.get("mode") or "").lower() == "paper_autopilot"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _autopilot_result_to_auto_close(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "auto_close_status": "paper_autopilot",
        "auto_close_candidates": int(result.get("autopilot_actions") or 0),
        "auto_close_attempted": int(result.get("autopilot_actions") or 0),
        "auto_close_skipped_existing": int(result.get("autopilot_close_skipped_existing") or 0),
        "auto_close_submitted": int(result.get("autopilot_close_submitted") or 0),
        "auto_close_dry_run": int(result.get("autopilot_close_dry_run") or 0),
        "auto_close_rejected": int(result.get("autopilot_close_rejected") or 0),
        "auto_close_error": int(result.get("autopilot_close_error") or 0),
        "auto_close_notes": str(result.get("autopilot_action_notes") or ""),
        **result,
    }


def execute_position_monitor_closes(
    positions: pd.DataFrame,
    decisions: pd.DataFrame,
    *,
    root: Path | None = None,
    active_order_symbols: set[str] | None = None,
    action_func: Callable[[str, str], dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute close decisions from the position monitor.

    When Paper Autopilot is running, the minute-by-minute monitor uses the
    Paper Autopilot close authority so profit giveback, defensive loss, hard
    stop, and explicit monitor close rules stay consistent overnight. Outside
    Paper Autopilot mode, it falls back to the older explicit monitor close
    path.
    """

    current_state = state or load_state(root)
    if not _paper_autopilot_active(current_state):
        return execute_monitor_auto_closes(
            decisions,
            active_order_symbols=active_order_symbols,
            action_func=action_func,
        )

    active_symbols = _clean_symbols(active_order_symbols)
    base_action = action_func or (lambda symbol, action: apply_manual_position_action(symbol, action))

    def guarded_action(symbol: str, action: str) -> dict[str, Any]:
        clean_symbol = str(symbol or "").strip().upper()
        if clean_symbol in active_symbols:
            return {
                "status": "skipped",
                "message": "active_order_exists",
                "symbol": clean_symbol,
                "operator_action": action,
            }
        return base_action(clean_symbol, action)

    update_position_peaks(current_state, positions)
    result = apply_paper_autopilot_decisions(root, positions, state=current_state, action_func=guarded_action)

    stamp = _now()
    current_state.update(result)
    current_state["updated_at"] = stamp
    current_state["last_tick_at"] = stamp
    current_state["open_positions"] = int(len(positions))
    submitted = int(result.get("autopilot_close_submitted") or 0)
    if submitted > 0:
        current_state["open_orders"] = max(int(current_state.get("open_orders") or 0), submitted)
        current_state["phase"] = "waiting_for_fills"
    elif str(current_state.get("phase") or "") not in {"waiting_for_fills", "guardrail_stop"}:
        current_state["phase"] = "monitoring_positions"
    saved = save_state(current_state, root)
    append_tick_log(saved, root)
    return _autopilot_result_to_auto_close(result)
