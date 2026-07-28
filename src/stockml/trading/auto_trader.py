from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from stockml.db.connection import _hydrate_environment
from stockml.trading.autopilot_guard import AUTOPILOT_BASKET_BLOCK_REASON
from stockml.trading.config import alpaca_config
from stockml.trading.execution_owner import normalize_execution_owner
from stockml.trading.paper_autopilot import context as paper_autopilot_context
from stockml.trading.paper_autopilot import tick as paper_autopilot_tick
from stockml.trading.paper_trader import refresh_order_tracking, run_paper_trading


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y"}


def auto_trading_enabled() -> bool:
    _hydrate_environment()
    return _bool_env("STOCKML_ALPACA_AUTOTRADE_ENABLED", default=True)


def _within_auto_trade_window(now: Optional[datetime] = None) -> bool:
    _hydrate_environment()
    if _bool_env("STOCKML_ALPACA_IGNORE_TRADE_WINDOW", default=False):
        return True
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    start = os.environ.get("STOCKML_ALPACA_AUTOTRADE_START_UTC", "14:45")
    end = os.environ.get("STOCKML_ALPACA_AUTOTRADE_END_UTC", "20:30")
    current = now.strftime("%H:%M")
    return start <= current <= end


def run_auto_trader(signal_file: Optional[Path] = None, force: bool = False) -> dict:
    enabled = auto_trading_enabled()
    in_window = _within_auto_trade_window()
    mode = "order_run" if enabled or force else "dry_run_only"
    owner = normalize_execution_owner(alpaca_config().execution_owner)
    try:
        if not enabled and not force:
            result = run_paper_trading(signal_file, plan_only=True)
            return {
                **result,
                "auto_trade_enabled": False,
                "auto_trade_mode": mode,
                "message": "STOCKML_ALPACA_AUTOTRADE_ENABLED is false; wrote a dry-run plan only.",
            }
        if not in_window and not force:
            tracking = refresh_order_tracking()
            return {
                **tracking,
                "auto_trade_enabled": enabled,
                "auto_trade_mode": "tracking_only",
                "message": "Outside configured auto-trade UTC window; refreshed tracking only.",
            }
        if owner == "paper_autopilot":
            state = paper_autopilot_tick()
            view = paper_autopilot_context()
            return {
                **state,
                "auto_trade_enabled": enabled,
                "auto_trade_mode": "paper_autopilot_tick",
                "execution_owner": owner,
                "open_orders": view.get("open_orders", state.get("open_orders", 0)),
                "open_positions": view.get("open_positions", state.get("open_positions", 0)),
                "last_error": view.get("last_error", state.get("last_error", "")),
                "message": "Auto-trader delegated submission to the configured Paper Autopilot owner.",
            }
        result = run_paper_trading(signal_file)
        return {
            **result,
            "auto_trade_enabled": enabled,
            "auto_trade_mode": mode,
            "execution_owner": owner,
            "message": "Auto-trader completed order run. Submission still depends on STOCKML_ALPACA_SUBMIT_ORDERS.",
        }
    except RuntimeError as exc:
        if str(exc) != AUTOPILOT_BASKET_BLOCK_REASON:
            raise
        tracking = refresh_order_tracking()
        return {
            **tracking,
            "auto_trade_enabled": enabled,
            "auto_trade_mode": "blocked_by_paper_autopilot",
            "block_reason": AUTOPILOT_BASKET_BLOCK_REASON,
            "message": "Paper Autopilot is running; skipped legacy basket submission and refreshed tracking only.",
        }
