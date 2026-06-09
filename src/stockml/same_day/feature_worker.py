from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import os
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from stockml.db.connection import get_engine
from stockml.db.schema import intraday_features
from stockml.intraday import kill_switch
from stockml.intraday.config import load_intraday_config
from stockml.intraday.provider import IntradayProvider
from stockml.intraday.worker import market_is_open
from stockml.same_day.features import compute_features, latest_feature_bar_frame, validate_cadence
from stockml.same_day.universe import build_same_day_universe


MARKET_TZ = ZoneInfo("America/New_York")
DEFAULT_MAX_SYMBOLS_PER_TICK = 300


@dataclass(frozen=True)
class FeatureRow:
    computed_at: datetime
    decision_time: datetime
    bar_close_at: datetime
    symbol: str
    status: str
    features: dict[str, Any]


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def current_5min_boundary(now: datetime | None = None) -> datetime:
    stamp = _aware(now)
    minute = stamp.minute - (stamp.minute % 5)
    return stamp.replace(minute=minute, second=0, microsecond=0)


def in_active_hours(now: datetime | None = None) -> bool:
    local = _aware(now).astimezone(MARKET_TZ)
    return time(10, 0) <= local.time() <= time(15, 0)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def feature_symbol_limit() -> int:
    return max(0, _env_int("STOCKML_SAME_DAY_FEATURE_MAX_SYMBOLS", DEFAULT_MAX_SYMBOLS_PER_TICK))


def write_feature_row(row: FeatureRow, *, engine: Engine | None = None) -> bool:
    db = engine or get_engine(required=True)
    with db.begin() as conn:
        try:
            conn.execute(insert(intraday_features).values(**asdict(row)))
            return True
        except IntegrityError:
            return False


def prune_old_features(*, engine: Engine | None = None, now: datetime | None = None, retention_days: int = 14) -> int:
    db = engine or get_engine(required=True)
    cutoff = _aware(now) - timedelta(days=retention_days)
    with db.begin() as conn:
        result = conn.execute(delete(intraday_features).where(intraday_features.c.computed_at < cutoff))
    return int(result.rowcount or 0)


def feature_tick(
    *,
    now: datetime | None = None,
    selected_date: date | None = None,
    provider: IntradayProvider | None = None,
    engine: Engine | None = None,
    universe_loader: Callable[[date], list[str]] | None = None,
    market_context_loader: Callable[[datetime], dict[str, Any]] | None = None,
    symbol_context_loader: Callable[[str], dict[str, Any]] | None = None,
    kill_switch_gate: Callable[..., kill_switch.KillSwitchVerdict] = kill_switch.gate,
) -> dict[str, Any]:
    stamp = _aware(now)
    cfg = load_intraday_config()
    validate_cadence(cfg.cadence_minutes)
    data_provider = provider or IntradayProvider()
    calendar = data_provider.fetch_market_calendar(selected_date or stamp.date())
    if not market_is_open(calendar, stamp):
        return {"status": "skipped", "reason": "market_closed", "features_written": 0}
    if not in_active_hours(stamp):
        return {"status": "skipped", "reason": "outside_same_day_hours", "features_written": 0}

    overnight_block = kill_switch.same_day_overnight_block(engine=engine)
    if overnight_block:
        return {
            "status": "skipped",
            "reason": "OVERNIGHT_POSITIONS_SAME_DAY",
            "features_written": 0,
            "overnight_positions": overnight_block,
        }

    verdict = kill_switch_gate(action="evaluate", engine=engine, now=stamp)
    if not verdict.allow:
        return {"status": "skipped", "reason": "kill_switch_active", "features_written": 0, "tripped": verdict.tripped}

    decision_time = current_5min_boundary(stamp)
    symbols = universe_loader(selected_date or stamp.date()) if universe_loader else build_same_day_universe(selected_date or stamp.date())
    max_symbols = feature_symbol_limit()
    if max_symbols:
        symbols = symbols[:max_symbols]
    market_context = market_context_loader(decision_time) if market_context_loader else {"open_at": calendar.open_at, "close_at": calendar.close_at}
    written = 0
    rows: list[FeatureRow] = []
    for symbol in symbols:
        clean = str(symbol).upper().strip()
        if not clean:
            continue
        try:
            quote = data_provider.fetch_quote(clean)
            bars = data_provider.fetch_bars(clean, timeframe=cfg.timeframe, limit=max(cfg.bar_limit, 24))
            symbol_context = symbol_context_loader(clean) if symbol_context_loader else {}
            feature_values = compute_features(clean, decision_time, quote, bars, market_context, symbol_context)
            if feature_values is None:
                row = FeatureRow(stamp, decision_time, decision_time - timedelta(minutes=5), clean, "data_unavailable", {})
            elif bool(feature_values.get("is_halted")):
                row = FeatureRow(stamp, decision_time, decision_time - timedelta(minutes=5), clean, "halted", feature_values)
            else:
                bar_frame = latest_feature_bar_frame(bars, decision_time)
                bar_close_at = bar_frame["timestamp"].iloc[-1].to_pydatetime() if not bar_frame.empty else decision_time - timedelta(minutes=5)
                row = FeatureRow(stamp, decision_time, _aware(bar_close_at), clean, "ok", feature_values)
        except Exception as exc:
            row = FeatureRow(stamp, decision_time, decision_time - timedelta(minutes=5), clean, "provider_error", {"error": str(exc)})
        rows.append(row)
        written += int(write_feature_row(row, engine=engine))
    return {"status": "ok", "reason": "", "symbols": symbols, "features_written": written, "rows": rows}
