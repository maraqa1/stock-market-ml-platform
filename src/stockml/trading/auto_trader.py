from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from stockml.db.connection import _hydrate_environment
from stockml.trading.paper_trader import refresh_order_tracking, run_paper_trading


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y"}


def auto_trading_enabled() -> bool:
    _hydrate_environment()
    return _bool_env("STOCKML_ALPACA_AUTOTRADE_ENABLED", default=False)


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
    if not enabled and not force:
        result = run_paper_trading(signal_file)
        return {
            **result,
            "auto_trade_enabled": False,
            "auto_trade_mode": "dry_run_only",
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
    result = run_paper_trading(signal_file)
    return {
        **result,
        "auto_trade_enabled": enabled,
        "auto_trade_mode": "order_run",
        "message": "Auto-trader completed order run. Submission still depends on STOCKML_ALPACA_SUBMIT_ORDERS.",
    }
