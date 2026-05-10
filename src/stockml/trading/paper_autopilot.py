from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.paper_trader import refresh_order_tracking


STATE_VERSION = 1
TERMINAL_ORDER_STATES = {"filled", "canceled", "cancelled", "expired", "rejected"}
OPEN_ORDER_STATES = {"accepted", "new", "pending_new", "pending_replace", "submitted", "partially_filled", "partial"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(root: Path | None = None) -> Path:
    if root is None:
        return PORTAL_OUTPUTS_DIR / "paper_autopilot_state.json"
    return Path(root) / "data" / "portal_outputs" / "paper_autopilot_state.json"


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "name": "Paper Autopilot",
        "status": "idle",
        "phase": "idle",
        "started_at": "",
        "updated_at": "",
        "stopped_at": "",
        "termination_reason": "",
        "last_tick_at": "",
        "last_error": "",
        "open_orders": 0,
        "broker_open_orders": 0,
        "tracked_open_orders": 0,
        "open_positions": 0,
        "orders_tracked": 0,
        "tracking_path": "",
        "positions_path": "",
        "paper_only": True,
        "live_trading_enabled": False,
    }


def load_state(root: Path | None = None) -> dict[str, Any]:
    state = _default_state()
    path = _state_path(root)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                state.update(stored)
        except Exception:
            state["last_error"] = "state_file_unreadable"
    return state


def save_state(state: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    out = _default_state()
    out.update(state)
    out["version"] = STATE_VERSION
    out["paper_only"] = True
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def start(root: Path | None = None) -> dict[str, Any]:
    cfg = alpaca_config()
    state = load_state(root)
    stamp = _now()
    state.update(
        {
            "status": "running",
            "phase": "tracking_orders",
            "started_at": state.get("started_at") or stamp,
            "updated_at": stamp,
            "stopped_at": "",
            "termination_reason": "",
            "last_error": "",
            "paper_only": True,
            "live_trading_enabled": bool(cfg.live_trading_enabled),
        }
    )
    if cfg.live_trading_enabled:
        state.update(
            {
                "status": "stopped",
                "phase": "guardrail_stop",
                "stopped_at": stamp,
                "termination_reason": "live_trading_enabled_guardrail",
                "last_error": "Live trading is enabled in config; Paper Autopilot refused to start.",
            }
        )
    return save_state(state, root)


def pause(root: Path | None = None) -> dict[str, Any]:
    state = load_state(root)
    if state.get("status") == "running":
        state.update({"status": "paused", "phase": "paused", "updated_at": _now()})
    return save_state(state, root)


def resume(root: Path | None = None) -> dict[str, Any]:
    state = load_state(root)
    if state.get("status") == "paused":
        state.update({"status": "running", "phase": "tracking_orders", "updated_at": _now(), "last_error": ""})
    return save_state(state, root)


def stop(root: Path | None = None, reason: str = "stopped_by_operator") -> dict[str, Any]:
    state = load_state(root)
    stamp = _now()
    state.update({"status": "stopped", "phase": "stopped", "updated_at": stamp, "stopped_at": stamp, "termination_reason": reason})
    return save_state(state, root)


def _read_csv(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _count_open_orders(tracking: pd.DataFrame) -> int:
    if tracking.empty:
        return 0
    status = tracking.get("alpaca_status", tracking.get("status", pd.Series("", index=tracking.index))).fillna("").astype(str).str.lower()
    fallback = tracking.get("status", pd.Series("", index=tracking.index)).fillna("").astype(str).str.lower()
    effective = status.where(status.ne(""), fallback)
    return int(effective.isin(OPEN_ORDER_STATES).sum())


def _count_broker_open_orders(cfg: Any) -> int:
    if not cfg.api_key or not cfg.secret_key:
        return 0
    orders = AlpacaPaperClient(cfg).list_orders(status="open")
    return len(orders)


def tick(
    root: Path | None = None,
    *,
    refresh_func: Callable[[], dict[str, Any]] = refresh_order_tracking,
    broker_open_orders_func: Callable[[Any], int] | None = None,
) -> dict[str, Any]:
    """Advance Paper Autopilot by one safe tracking step.

    The first implementation deliberately tracks broker/position state and
    terminates cleanly. It does not auto-apply close/rotate/resize decisions.
    """
    state = load_state(root)
    if state.get("status") != "running":
        state.update({"last_tick_at": _now(), "last_error": "autopilot_not_running"})
        return save_state(state, root)

    cfg = alpaca_config()
    if cfg.live_trading_enabled:
        return stop(root, reason="live_trading_enabled_guardrail")

    stamp = _now()
    try:
        refreshed = refresh_func()
        tracking = _read_csv(refreshed.get("tracking_path"))
        positions = _read_csv(refreshed.get("positions_path"))
        tracked_open_orders = _count_open_orders(tracking)
        broker_open_orders = (broker_open_orders_func or _count_broker_open_orders)(cfg)
        open_orders = max(tracked_open_orders, broker_open_orders)
        open_positions = int(len(positions))
        if open_orders > 0:
            phase = "waiting_for_fills"
            status = "running"
            termination_reason = ""
        elif open_positions > 0:
            phase = "monitoring_positions"
            status = "running"
            termination_reason = ""
        else:
            phase = "cycle_complete"
            status = "complete"
            termination_reason = "no_open_orders_or_positions"
        state.update(
            {
                "status": status,
                "phase": phase,
                "updated_at": stamp,
                "last_tick_at": stamp,
                "last_error": "",
                "termination_reason": termination_reason,
                "open_orders": open_orders,
                "broker_open_orders": broker_open_orders,
                "tracked_open_orders": tracked_open_orders,
                "open_positions": open_positions,
                "orders_tracked": int(refreshed.get("orders_tracked") or 0),
                "tracking_path": str(refreshed.get("tracking_path") or ""),
                "positions_path": str(refreshed.get("positions_path") or ""),
                "live_trading_enabled": False,
            }
        )
    except Exception as exc:
        state.update({"status": "stopped", "phase": "error", "updated_at": stamp, "stopped_at": stamp, "last_error": str(exc), "termination_reason": "autopilot_error"})
    return save_state(state, root)


def action(action_name: str, root: Path | None = None) -> dict[str, Any]:
    clean = str(action_name or "").strip().lower()
    if clean == "start":
        return start(root)
    if clean == "pause":
        return pause(root)
    if clean == "resume":
        return resume(root)
    if clean == "stop":
        return stop(root)
    if clean == "tick":
        return tick(root)
    state = load_state(root)
    state.update({"last_error": f"unsupported_autopilot_action:{clean}", "updated_at": _now()})
    return save_state(state, root)


def context(root: Path | None = None) -> dict[str, Any]:
    if root is None:
        ensure_data_dirs()
    state = load_state(root)
    labels = {
        "idle": "Autopilot Idle",
        "running": "Autopilot Running",
        "paused": "Autopilot Paused",
        "stopped": "Autopilot Stopped",
        "complete": "Cycle Complete",
    }
    phase_labels = {
        "idle": "Idle",
        "tracking_orders": "Tracking Orders",
        "waiting_for_fills": "Waiting for Fills",
        "monitoring_positions": "Monitoring Positions",
        "cycle_complete": "Cycle Complete",
        "paused": "Paused",
        "stopped": "Stopped by Operator",
        "guardrail_stop": "Guardrail Stop",
        "error": "Error",
    }
    return {
        **state,
        "status_label": labels.get(str(state.get("status") or ""), str(state.get("status") or "idle").replace("_", " ").title()),
        "phase_label": phase_labels.get(str(state.get("phase") or ""), str(state.get("phase") or "idle").replace("_", " ").title()),
        "state_path": str(_state_path(root)),
    }
