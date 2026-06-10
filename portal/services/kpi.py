from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from portal.services.account import account_snapshot
from portal.services.trading_api_service import (
    action_queue_context,
    basket_today_context,
    pipeline_current_context,
    positions_context,
)
from stockml.trading.config import alpaca_config
from stockml.trading.timer_settings import load_timer_settings, seconds_label


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


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


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


def trading_cadence_context(root: Path, *, pipeline: dict[str, Any] | None = None) -> dict[str, Any]:
    timers = load_timer_settings(root)
    pipeline = pipeline if pipeline is not None else pipeline_current_context(root)
    run = pipeline.get("run") or {}
    next_monitor = _next_seconds_interval(timers["monitor_interval_seconds"])
    last_run = run.get("started_at") or run.get("run_id") or "No recorded run"
    return {
        "positions_label": f"live ({timers['positions_refresh_seconds']}s)",
        "positions_refresh_ms": int(timers["positions_refresh_seconds"]) * 1000,
        "monitor_label": seconds_label(timers["monitor_interval_seconds"]),
        "next_monitor_at": next_monitor.isoformat(),
        "next_monitor_label": next_monitor.strftime("%H:%M:%S UTC"),
        "pipeline_label": "nightly",
        "pipeline_refresh_label": seconds_label(timers["pipeline_refresh_seconds"]),
        "pipeline_refresh_ms": int(timers["pipeline_refresh_seconds"]) * 1000,
        "last_run_label": str(last_run),
    }


def trading_kpi_context(
    root: Path,
    *,
    positions: dict[str, Any] | None = None,
    basket: dict[str, Any] | None = None,
    queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = alpaca_config()
    account = account_snapshot(config)
    positions = positions if positions is not None else positions_context(root)
    basket = basket if basket is not None else basket_today_context(root)
    queue = queue if queue is not None else action_queue_context(root)
    summary = positions["summary"]
    position_count = int(summary.get("position_count") or 0)
    gross_market_value = float(summary.get("position_market_value") or 0.0)
    net_market_value = float(summary.get("position_net_market_value") or 0.0)
    unrealized_pl = float(summary.get("position_unrealized_pl") or 0.0)
    unrealized_plpc = float(summary.get("position_unrealized_plpc") or 0.0)
    pending = int(queue["counts"].get("action_required", queue["counts"].get("total")) or 0)
    submitted = int(basket["counts"].get("submitted") or 0)
    filled = int(basket["counts"].get("filled") or 0)
    account_equity = float(account.get("equity") or config.account_equity or 0.0)
    account_detail = "Alpaca paper account" if account.get("source") == "alpaca" else "configured fallback"
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
                "value": f"${account_equity:,.0f}",
                "detail": account_detail,
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
                "label": "Gross Exposure",
                "value": _money(gross_market_value),
                "detail": (
                    f"Net {_money(net_market_value)} - {(gross_market_value / account_equity):.2%} gross of equity"
                    if account_equity
                    else f"Net {_money(net_market_value)} - equity unavailable"
                ),
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
