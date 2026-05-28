from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import pstdev
from typing import Any, Callable

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from stockml.db.connection import get_engine
from stockml.db.schema import ensure_intraday_candidate_snapshot_float_columns, intraday_candidate_snapshots, intraday_promotion_log
from stockml.intraday import kill_switch
from stockml.intraday.config import load_intraday_config
from stockml.intraday.features import Bar, Quote
from stockml.intraday.logging import intraday_log
from stockml.intraday.provider import IntradayProvider, MarketCalendar
from stockml.intraday.scope import PositionLoader, scope_rows_for_today
from stockml.intraday.worker import default_market_context, market_is_open


@dataclass(frozen=True)
class CandidateSnapshot:
    snapshot_at: datetime
    bar_close_at: datetime
    symbol: str
    nightly_score: float | None
    nightly_bias: str | None
    is_held: bool
    bid: float | None
    ask: float | None
    last_price: float | None
    spread_bps: float | None
    quote_age_sec: int | None
    dollar_volume_today: float | None
    liquidity_ratio: float | None
    trend_5m_pct: float | None
    trend_15m_pct: float | None
    trend_30m_pct: float | None
    vwap_today: float | None
    distance_from_vwap_bps: float | None
    intraday_range_position: float | None
    volatility_burst: bool
    sector_etf_trend_5m_pct: float | None
    market_aligned: bool | None
    status: str
    details: dict[str, Any]


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _trend_pct(closes: list[float], lookback_bars: int) -> float | None:
    if len(closes) <= lookback_bars:
        return None
    previous = closes[-1 - lookback_bars]
    if previous == 0:
        return None
    return (closes[-1] - previous) / previous * 100


def _bar_close_at(now: datetime, bars: list[Bar]) -> datetime:
    for bar in reversed(bars):
        if bar.timestamp:
            return _aware(bar.timestamp)
    stamp = _aware(now)
    minute = stamp.minute - (stamp.minute % 5)
    return stamp.replace(minute=minute, second=0, microsecond=0)


def build_snapshot(
    scope_row: dict[str, Any],
    quote: Quote | None,
    bars: list[Bar] | None,
    market_context: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    status: str = "ok",
    details: dict[str, Any] | None = None,
) -> CandidateSnapshot:
    stamp = _aware(now)
    bars = list(bars or [])
    market_context = market_context or {}
    symbol = str(scope_row.get("symbol") or getattr(quote, "symbol", "")).strip().upper()
    quote = quote or Quote(symbol=symbol, fetched_at=stamp)

    bid = _safe_float(quote.bid)
    ask = _safe_float(quote.ask)
    closes = [_safe_float(bar.close) for bar in bars]
    closes = [value for value in closes if value is not None]
    last_price = _safe_float(quote.last_price) or (closes[-1] if closes else None)
    mid = (bid + ask) / 2 if bid is not None and ask is not None and (bid + ask) else last_price
    spread_bps = ((ask - bid) / mid * 10_000) if bid is not None and ask is not None and mid else None

    quote_ts = quote.quote_ts
    if quote_ts and quote_ts.tzinfo is None:
        quote_ts = quote_ts.replace(tzinfo=timezone.utc)
    quote_age = int((stamp - quote_ts).total_seconds()) if quote_ts else None

    volumes = [_safe_float(bar.volume) for bar in bars]
    volumes = [value for value in volumes if value is not None]
    dollar_volume_today = sum((bar.close or 0) * (bar.volume or 0) for bar in bars) if bars else None
    avg_dollar_volume_20d = _safe_float(scope_row.get("avg_dollar_volume_20d"))

    vwaps = [_safe_float(bar.vwap) for bar in bars]
    vwaps = [value for value in vwaps if value is not None]
    vwap_today = vwaps[-1] if vwaps else None
    distance_from_vwap_bps = ((last_price - vwap_today) / vwap_today * 10_000) if last_price is not None and vwap_today not in {None, 0} else None

    highs = [_safe_float(bar.high) for bar in bars]
    lows = [_safe_float(bar.low) for bar in bars]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    range_position = None
    if last_price is not None and highs and lows and max(highs) != min(lows):
        range_position = (last_price - min(lows)) / (max(highs) - min(lows))

    returns = [(curr - prev) / prev for prev, curr in zip(closes, closes[1:]) if prev]
    realized_vol = pstdev(returns) * 10_000 if len(returns) > 1 else None
    median_5d = _safe_float(scope_row.get("realized_vol_60m_bps_median_5d"))
    scope_details = {}
    for key in (
        "source",
        "strategy_stream",
        "trading_stream",
        "same_day_momentum",
        "same_day_trade_action",
        "same_day_confidence",
        "same_day_reason",
        "max_hold_days",
        "must_flatten_eod",
        "manual_move_pct",
        "manual_last_price",
        "manual_dollar_traded",
    ):
        value = scope_row.get(key)
        if value is None or value == "":
            continue
        scope_details[key] = value
    if scope_details.get("same_day_momentum"):
        scope_details.setdefault("current_trade_action", scope_details.get("same_day_trade_action"))
        scope_details.setdefault("side", "sell" if str(scope_details.get("same_day_trade_action")).lower() == "short" else "buy")
        scope_details.setdefault("nightly_bias", str(scope_row.get("bias") or scope_row.get("nightly_bias") or "long").lower())

    return CandidateSnapshot(
        snapshot_at=stamp,
        bar_close_at=_bar_close_at(stamp, bars),
        symbol=symbol,
        nightly_score=_safe_float(scope_row.get("score") or scope_row.get("nightly_score")),
        nightly_bias=str(scope_row.get("bias") or scope_row.get("nightly_bias") or "neutral").lower(),
        is_held=bool(scope_row.get("is_held", False)),
        bid=bid,
        ask=ask,
        last_price=last_price,
        spread_bps=spread_bps,
        quote_age_sec=quote_age,
        dollar_volume_today=dollar_volume_today,
        liquidity_ratio=_safe_div(dollar_volume_today, avg_dollar_volume_20d),
        trend_5m_pct=_trend_pct(closes, 1),
        trend_15m_pct=_trend_pct(closes, 3),
        trend_30m_pct=_trend_pct(closes, 6),
        vwap_today=vwap_today,
        distance_from_vwap_bps=distance_from_vwap_bps,
        intraday_range_position=range_position,
        volatility_burst=bool(realized_vol is not None and median_5d not in {None, 0} and realized_vol > 2 * median_5d),
        sector_etf_trend_5m_pct=_safe_float(market_context.get("sector_etf_trend_5m_pct") or market_context.get("sector_etf_trend_5m")),
        market_aligned=market_context.get("market_aligned"),
        status=status,
        details={**scope_details, **(details or {})},
    )


def write_snapshot(snapshot: CandidateSnapshot, *, engine: Engine | None = None) -> bool:
    db = engine or get_engine(required=True)
    ensure_intraday_candidate_snapshot_float_columns(db)
    row = asdict(snapshot)
    with db.begin() as conn:
        exists = conn.execute(
            select(intraday_candidate_snapshots.c.id)
            .where(intraday_candidate_snapshots.c.symbol == snapshot.symbol)
            .where(intraday_candidate_snapshots.c.bar_close_at == snapshot.bar_close_at)
            .limit(1)
        ).first()
        if exists:
            return False
        try:
            conn.execute(insert(intraday_candidate_snapshots).values(**row))
            return True
        except IntegrityError:
            return False


def prune_old_snapshots(*, engine: Engine | None = None, now: datetime | None = None, retention_days: int = 7) -> int:
    db = engine or get_engine(required=True)
    cutoff = _aware(now) - timedelta(days=retention_days)
    old_snapshot_ids = select(intraday_candidate_snapshots.c.id).where(intraday_candidate_snapshots.c.snapshot_at < cutoff)
    with db.begin() as conn:
        conn.execute(delete(intraday_promotion_log).where(intraday_promotion_log.c.snapshot_id.in_(old_snapshot_ids)))
        result = conn.execute(delete(intraday_candidate_snapshots).where(intraday_candidate_snapshots.c.snapshot_at < cutoff))
    return int(result.rowcount or 0)


def candidate_refresh_tick(
    *,
    now: datetime | None = None,
    selected_date: date | None = None,
    provider: IntradayProvider | None = None,
    engine: Engine | None = None,
    positions_loader: PositionLoader | None = None,
    scope_loader: Callable[..., list[dict[str, Any]]] = scope_rows_for_today,
    market_context_loader: Callable[[MarketCalendar, datetime], dict[str, Any]] = default_market_context,
    kill_switch_gate: Callable[..., kill_switch.KillSwitchVerdict] = kill_switch.gate,
) -> dict[str, Any]:
    stamp = _aware(now)
    cfg = load_intraday_config()
    if not cfg.enabled:
        intraday_log("candidate_refresh_skipped", {"reason": "intraday_disabled"}, now=stamp)
        return {"status": "skipped", "reason": "intraday_disabled", "snapshots_written": 0}

    data_provider = provider or IntradayProvider()
    calendar = data_provider.fetch_market_calendar(selected_date or stamp.date())
    if not market_is_open(calendar, stamp):
        intraday_log("candidate_refresh_skipped", {"reason": "market_closed"}, now=stamp)
        return {"status": "skipped", "reason": "market_closed", "snapshots_written": 0}

    verdict = kill_switch_gate(action="evaluate", engine=engine, now=stamp)
    if not verdict.allow:
        intraday_log("candidate_refresh_skipped", {"reason": "kill_switch_active", "tripped": verdict.tripped}, now=stamp)
        return {"status": "skipped", "reason": "kill_switch_active", "tripped": verdict.tripped, "snapshots_written": 0}

    rows = scope_loader(selected_date or stamp.date(), positions_loader=positions_loader, engine=engine)
    market_context = market_context_loader(calendar, stamp)
    snapshots_written = 0
    symbols: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        symbols.append(symbol)
        try:
            quote = data_provider.fetch_quote(symbol)
            bars = data_provider.fetch_bars(symbol, timeframe=cfg.timeframe, limit=cfg.bar_limit)
            status = "ok" if quote is not None and bars is not None else "data_unavailable"
            snapshot = build_snapshot(row, quote, bars, market_context, now=stamp, status=status)
        except Exception as exc:
            snapshot = build_snapshot(row, None, [], market_context, now=stamp, status="provider_error", details={"error": str(exc)})
            intraday_log("candidate_refresh_symbol_error", {"symbol": symbol, "error": str(exc)}, now=stamp)
        snapshots_written += int(write_snapshot(snapshot, engine=engine))

    return {"status": "ok", "reason": "", "symbols": symbols, "snapshots_written": snapshots_written}
