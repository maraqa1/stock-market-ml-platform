from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import csv

from stockml.common.paths import PORTAL_OUTPUTS_DIR


AUTOPILOT_BASKET_BLOCK_REASON = "paper_autopilot_running_blocks_basket_submission"
AUTOPILOT_SYMBOL_CONFLICT_REASON = "paper_autopilot_symbol_conflict"


def autopilot_state_path() -> Path:
    return PORTAL_OUTPUTS_DIR / "paper_autopilot_state.json"


def load_autopilot_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or autopilot_state_path()
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_autopilot_state(state: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    state_path = path or autopilot_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def autopilot_blocks_basket_submission(path: Path | None = None) -> tuple[bool, str]:
    state = load_autopilot_state(path)
    if str(state.get("status", "")).lower() == "running" and str(state.get("mode", "")).lower() == "paper_autopilot":
        return True, AUTOPILOT_BASKET_BLOCK_REASON
    return False, ""


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _symbols_from_csv(path_value: object, *, open_only: bool = False) -> set[str]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return set()
    path = Path(path_text)
    if not path.exists() or path.stat().st_size == 0:
        return set()
    symbols: set[str] = set()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if open_only:
                    status = str(row.get("alpaca_status") or row.get("status") or "").strip().lower()
                    if status not in {"new", "accepted", "pending_new", "partially_filled"}:
                        continue
                symbol = _clean_symbol(row.get("symbol") or row.get("ticker"))
                if symbol:
                    symbols.add(symbol)
    except Exception:
        return set()
    return symbols


def _csv_row_count(path_value: object) -> int:
    path_text = str(path_value or "").strip()
    if not path_text:
        return 0
    path = Path(path_text)
    if not path.exists() or path.stat().st_size <= 1:
        return 0
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except Exception:
        return 0


def reconcile_autopilot_state_from_tracking(
    *,
    tracking_path: Path | str | None,
    positions_path: Path | str | None,
    orders_tracked: int = 0,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Update Paper Autopilot state from the latest broker tracking snapshot.

    The broker snapshot is authoritative for flatness. If the latest tracking
    and positions files show no open broker orders and no positions, clear stale
    managed-symbol memory so a previous cycle cannot block a new basket.
    """
    state = load_autopilot_state(state_path)
    if str(state.get("status", "")).lower() != "running" or str(state.get("mode", "")).lower() != "paper_autopilot":
        return state

    open_order_symbols = _symbols_from_csv(tracking_path, open_only=True)
    position_count = _csv_row_count(positions_path)
    open_order_count = len(open_order_symbols)
    state.update(
        {
            "open_positions": position_count,
            "open_orders": open_order_count,
            "broker_open_orders": open_order_count,
            "tracked_open_orders": open_order_count,
            "orders_tracked": int(orders_tracked or 0),
            "tracking_path": str(tracking_path or ""),
            "positions_path": str(positions_path or ""),
        }
    )
    if position_count == 0 and open_order_count == 0:
        state.update(
            {
                "phase": "tracking_orders",
                "termination_reason": "",
                "eod_dispositions": [],
                "eod_remaining": 0,
                "eod_banner": "",
                "position_peak_plpc": {},
                "autopilot_action_notes": "",
                "last_error": "",
            }
        )
    return save_autopilot_state(state, state_path)


def autopilot_managed_symbols(path: Path | None = None) -> set[str]:
    state = load_autopilot_state(path)
    if str(state.get("status", "")).lower() != "running" or str(state.get("mode", "")).lower() != "paper_autopilot":
        return set()

    symbols: set[str] = set()
    for item in state.get("eod_dispositions") or []:
        if isinstance(item, dict):
            symbol = _clean_symbol(item.get("symbol") or item.get("ticker"))
            if symbol:
                symbols.add(symbol)
    peak_map = state.get("position_peak_plpc")
    if isinstance(peak_map, dict):
        symbols.update(_clean_symbol(symbol) for symbol in peak_map.keys() if _clean_symbol(symbol))
    symbols.update(_symbols_from_csv(state.get("positions_path")))
    symbols.update(_symbols_from_csv(state.get("tracking_path"), open_only=True))
    return symbols


def autopilot_conflicting_symbols(symbols: set[str], path: Path | None = None) -> tuple[set[str], str]:
    state = load_autopilot_state(path)
    if str(state.get("status", "")).lower() != "running" or str(state.get("mode", "")).lower() != "paper_autopilot":
        return set(), ""
    requested = {_clean_symbol(symbol) for symbol in symbols if _clean_symbol(symbol)}
    managed = autopilot_managed_symbols(path)
    if not managed and (int(state.get("open_positions") or 0) > 0 or int(state.get("open_orders") or state.get("tracked_open_orders") or 0) > 0):
        return requested, AUTOPILOT_BASKET_BLOCK_REASON
    conflicts = requested & managed
    return conflicts, AUTOPILOT_SYMBOL_CONFLICT_REASON if conflicts else ""
