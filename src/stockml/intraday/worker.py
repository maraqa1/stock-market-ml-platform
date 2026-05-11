from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy.engine import Engine

from stockml.intraday import kill_switch
from stockml.intraday.config import load_intraday_config
from stockml.intraday.decisions import record_decision
from stockml.intraday.features import NightlySignal, compute_features
from stockml.intraday.gates import evaluate_gate
from stockml.intraday.logging import intraday_log
from stockml.intraday.provider import IntradayProvider, MarketCalendar
from stockml.intraday.scope import PositionLoader, scope_for_today


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def market_is_open(calendar: MarketCalendar, now: datetime | None = None) -> bool:
    stamp = _aware(now)
    if not calendar.is_open or calendar.open_at is None or calendar.close_at is None:
        return False
    open_at = _aware(calendar.open_at)
    close_at = _aware(calendar.close_at)
    return open_at <= stamp <= close_at


def default_market_context(calendar: MarketCalendar, now: datetime) -> dict[str, Any]:
    return {
        "now": now,
        "open_at": calendar.open_at,
        "close_at": calendar.close_at,
        "is_first_15_min": bool(calendar.open_at and 0 <= (now - _aware(calendar.open_at)).total_seconds() < 15 * 60),
        "is_last_30_min": bool(calendar.close_at and 0 <= (_aware(calendar.close_at) - now).total_seconds() < 30 * 60),
        "vix_regime": "normal",
        "spy_intraday_trend_5m": 0.0,
        "sector_concurrent_move": False,
    }


def intraday_tick(
    *,
    now: datetime | None = None,
    selected_date: date | None = None,
    provider: IntradayProvider | None = None,
    engine: Engine | None = None,
    positions_loader: PositionLoader | None = None,
    scope_loader: Callable[..., list[str]] = scope_for_today,
    nightly_signal_loader: Callable[[str], NightlySignal | dict | None] | None = None,
    position_context_loader: Callable[[str], dict[str, Any]] | None = None,
    market_context_loader: Callable[[MarketCalendar, datetime], dict[str, Any]] = default_market_context,
    kill_switch_gate: Callable[..., kill_switch.KillSwitchVerdict] = kill_switch.gate,
) -> dict[str, Any]:
    stamp = _aware(now)
    cfg = load_intraday_config()
    if not cfg.enabled:
        intraday_log("intraday_tick_skipped", {"reason": "intraday_disabled"}, now=stamp)
        return {"status": "skipped", "reason": "intraday_disabled", "decisions_written": 0}

    data_provider = provider or IntradayProvider()
    calendar = data_provider.fetch_market_calendar(selected_date or stamp.date())
    if not market_is_open(calendar, stamp):
        intraday_log("intraday_tick_skipped", {"reason": "market_closed"}, now=stamp)
        return {"status": "skipped", "reason": "market_closed", "decisions_written": 0}

    verdict = kill_switch_gate(action="evaluate", engine=engine, now=stamp)
    if not verdict.allow:
        intraday_log("intraday_tick_skipped", {"reason": "kill_switch_active", "tripped": verdict.tripped}, now=stamp)
        return {"status": "skipped", "reason": "kill_switch_active", "tripped": verdict.tripped, "decisions_written": 0}

    symbols = scope_loader(selected_date or stamp.date(), positions_loader=positions_loader, engine=engine)
    market_context = market_context_loader(calendar, stamp)
    rows = []
    for symbol in symbols:
        nightly_signal = nightly_signal_loader(symbol) if nightly_signal_loader else None
        position_context = position_context_loader(symbol) if position_context_loader else {}
        try:
            quote = data_provider.fetch_quote(symbol)
            bars = data_provider.fetch_bars(symbol, timeframe=cfg.timeframe, limit=cfg.bar_limit)
            features = compute_features(symbol, quote, bars, market_context, {"preserved_bias": getattr(nightly_signal, "normalized_bias", None)}, position_context)
            decision = evaluate_gate(features, nightly_signal)
            rows.append(record_decision(symbol, features, decision, nightly_signal, engine=engine, decided_at=stamp, bar_close_at=stamp))
        except Exception as exc:
            rows.append(record_decision(symbol, {}, None, nightly_signal, engine=engine, decided_at=stamp, bar_close_at=stamp, status="data_unavailable"))
            intraday_log("intraday_symbol_error", {"symbol": symbol, "error": str(exc)}, now=stamp)
    return {"status": "ok", "reason": "", "symbols": symbols, "decisions_written": len(rows), "rows": rows}

