from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from portal.services.trading_api_service import (
    action_queue_context,
    basket_today_context,
    pipeline_current_context,
    positions_context,
)
from stockml.trading.config import alpaca_config


def _next_interval(minutes: int = 10) -> datetime:
    now = datetime.now(timezone.utc)
    minute = ((now.minute // minutes) + 1) * minutes
    base = now.replace(second=0, microsecond=0)
    if minute >= 60:
        return base.replace(minute=0) + timedelta(hours=1)
    return base.replace(minute=minute)


def _next_seconds_interval(seconds: int) -> datetime:
    now = datetime.now(timezone.utc)
    base = now.replace(microsecond=0)
    remainder = base.second % seconds
    delta = seconds - remainder if remainder else seconds
    return base + timedelta(seconds=delta)


def _signed_class(value: float) -> str:
    if value > 0:
        return "text-up"
    if value < 0:
        return "text-down"
    return ""


def trading_header_context(root: Path) -> dict[str, Any]:
    config = alpaca_config()
    return {
        "title": "Paper Trading - Research Mode",
        "account_label": "StockML operator console",
        "mode_label": "Dry Run" if not config.submit_orders else "Paper Submission Enabled",
        "paper_trading_enabled": bool(config.paper_trading_enabled),
        "live_trading_enabled": bool(config.live_trading_enabled),
        "live_label": "Live Trading Enabled" if config.live_trading_enabled else "Live Trading Disabled",
    }


def trading_cadence_context(root: Path) -> dict[str, Any]:
    pipeline = pipeline_current_context(root)
    run = pipeline.get("run") or {}
    next_monitor = _next_seconds_interval(30)
    last_run = run.get("started_at") or run.get("run_id") or "No recorded run"
    return {
        "positions_label": "live (5s)",
        "monitor_label": "every 30s",
        "next_monitor_at": next_monitor.isoformat(),
        "next_monitor_label": next_monitor.strftime("%H:%M:%S UTC"),
        "pipeline_label": "nightly",
        "last_run_label": str(last_run),
    }


def trading_kpi_context(root: Path) -> dict[str, Any]:
    config = alpaca_config()
    positions = positions_context(root)
    basket = basket_today_context(root)
    queue = action_queue_context(root)
    summary = positions["summary"]
    position_count = int(summary.get("position_count") or 0)
    market_value = float(summary.get("position_market_value") or 0.0)
    unrealized_pl = float(summary.get("position_unrealized_pl") or 0.0)
    unrealized_plpc = float(summary.get("position_unrealized_plpc") or 0.0)
    pending = int(queue["counts"].get("total") or 0)
    submitted = int(basket["counts"].get("submitted") or 0)
    filled = int(basket["counts"].get("filled") or 0)
    return {
        "cards": [
            {
                "label": "Open Positions",
                "value": f"{position_count} / {config.max_orders}",
                "detail": f"Submitted {submitted}, filled {filled}",
                "href": "#open-positions",
            },
            {
                "label": "Account Equity",
                "value": f"${config.account_equity:,.0f}",
                "detail": "configured paper account base",
            },
            {
                "label": "Unrealized P&L",
                "value": f"{unrealized_pl:+,.2f}",
                "detail": f"{unrealized_plpc:+.2%}",
                "class": _signed_class(unrealized_pl),
            },
            {
                "label": "Today's P&L",
                "value": f"{unrealized_pl:+,.2f}",
                "detail": "paper mark-to-market proxy",
                "class": _signed_class(unrealized_pl),
            },
            {
                "label": "Net Exposure",
                "value": f"${market_value:,.0f}",
                "detail": f"{(market_value / config.account_equity):.2%} of equity" if config.account_equity else "equity unavailable",
            },
            {
                "label": "Pending Decisions",
                "value": str(pending),
                "detail": "Review action queue",
                "href": "#action-queue",
                "alert": pending > 0,
            },
        ]
    }
