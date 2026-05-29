from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from stockml.intraday.features import Bar, Quote

FEATURE_STATUS = {"ok", "data_unavailable", "provider_error", "halted", "out_of_universe"}
MIN_INTRADAY_CADENCE_MIN = 5


class ConfigurationError(ValueError):
    pass


def _utc(value: Any) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _num(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(parsed)


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def time_of_day_bucket(decision_time: datetime | pd.Timestamp) -> int:
    stamp = _utc(decision_time).tz_convert("America/New_York")
    minutes = stamp.hour * 60 + stamp.minute
    if minutes < 10 * 60:
        return 0
    if minutes < 11 * 60:
        return 1
    if minutes < 14 * 60:
        return 2
    if minutes < 15 * 60:
        return 3
    return 4


def bars_to_frame(bars: list[Bar] | pd.DataFrame | None) -> pd.DataFrame:
    if bars is None:
        return pd.DataFrame()
    if isinstance(bars, pd.DataFrame):
        frame = bars.copy()
    else:
        frame = pd.DataFrame(
            [
                {
                    "timestamp": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "vwap": bar.vwap,
                }
                for bar in bars
            ]
        )
    if frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "vwap"]:
        if column not in frame.columns:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def validate_cadence(cadence_minutes: int) -> None:
    if int(cadence_minutes) < MIN_INTRADAY_CADENCE_MIN:
        raise ConfigurationError(f"same-day cadence must be at least {MIN_INTRADAY_CADENCE_MIN} minutes")


def latest_feature_bar_frame(bars: list[Bar] | pd.DataFrame | None, decision_time: datetime | pd.Timestamp) -> pd.DataFrame:
    frame = bars_to_frame(bars)
    if frame.empty:
        return frame
    latest_allowed = _utc(decision_time) - pd.Timedelta(minutes=5)
    return frame[frame["timestamp"] <= latest_allowed].copy().reset_index(drop=True)


def _log_return(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return float(math.log(end / start))


def _bar_at_or_before(history: pd.DataFrame, stamp: pd.Timestamp) -> pd.Series | None:
    rows = history[history["timestamp"] <= stamp]
    if rows.empty:
        return None
    return rows.iloc[-1]


def _consecutive_same_direction(history: pd.DataFrame, direction: int) -> int:
    count = 0
    for _, row in history.iloc[::-1].iterrows():
        opened = _num(row.get("open"))
        closed = _num(row.get("close"))
        if opened is None or closed is None or opened == closed:
            break
        if (closed - opened > 0 and direction > 0) or (closed - opened < 0 and direction < 0):
            count += 1
            continue
        break
    return count


def _seconds_since_signal_first_fired(
    history: pd.DataFrame,
    latest_allowed: pd.Timestamp,
    *,
    direction: int,
    avg_volume_by_bar: dict[str, Any],
) -> int | None:
    if direction == 0 or len(history) < 4:
        return None
    fallback_volume = _num(history["volume"].dropna().tail(20).mean()) or 1.0
    for idx in range(3, len(history)):
        row = history.iloc[idx]
        prior = history.iloc[idx - 3]
        close = _num(row.get("close"))
        prior_open = _num(prior.get("open"))
        signal_return = _log_return(prior_open, close)
        if signal_return is None or (signal_return > 0) != (direction > 0):
            continue
        row_ts = _utc(row.get("timestamp"))
        bar_key = row_ts.tz_convert("America/New_York").strftime("%H:%M")
        avg_volume = _num(avg_volume_by_bar.get(bar_key)) if isinstance(avg_volume_by_bar, dict) else None
        relative_volume = _safe_div(_num(row.get("volume")), avg_volume or fallback_volume)
        if relative_volume is not None and relative_volume > 1.5:
            return max(0, int((latest_allowed - row_ts).total_seconds()))
    return None


def compute_features(
    symbol: str,
    decision_time: datetime | pd.Timestamp,
    quote: Quote | None,
    bars: list[Bar] | pd.DataFrame | None,
    market_context: dict[str, Any] | None = None,
    symbol_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    market_context = market_context or {}
    symbol_context = symbol_context or {}
    decision = _utc(decision_time)
    latest_allowed = decision - pd.Timedelta(minutes=5)
    history = latest_feature_bar_frame(bars, decision)
    if len(history) < 2:
        return None

    last = history.iloc[-1]
    prev_20 = _bar_at_or_before(history, latest_allowed - pd.Timedelta(minutes=15))
    prev_35 = _bar_at_or_before(history, latest_allowed - pd.Timedelta(minutes=30))
    prev_65 = _bar_at_or_before(history, latest_allowed - pd.Timedelta(minutes=60))

    last_open = _num(last.get("open"))
    last_close = _num(last.get("close"))
    return_5m = _log_return(last_open, last_close)
    return_15m = _log_return(_num(prev_20.get("open")) if prev_20 is not None else None, last_close)
    return_30m = _log_return(_num(prev_35.get("open")) if prev_35 is not None else None, last_close)
    return_60m = _log_return(_num(prev_65.get("open")) if prev_65 is not None else None, last_close)

    bar_key = latest_allowed.tz_convert("America/New_York").strftime("%H:%M")
    avg_volume_by_bar = symbol_context.get("average_volume_by_bar") or {}
    avg_bar_volume = _num(avg_volume_by_bar.get(bar_key)) if isinstance(avg_volume_by_bar, dict) else None
    if avg_bar_volume is None:
        same_time = history[history["timestamp"].dt.tz_convert("America/New_York").dt.strftime("%H:%M").eq(bar_key)]
        avg_bar_volume = _num(same_time["volume"].mean()) if not same_time.empty else None
    last_volume = _num(last.get("volume"))
    relative_volume = _safe_div(last_volume, avg_bar_volume)

    window_15 = history[history["timestamp"] >= latest_allowed - pd.Timedelta(minutes=15)]
    dollar_volume_15m = float((window_15["close"].fillna(0) * window_15["volume"].fillna(0)).sum()) if not window_15.empty else None
    dollar_volume_today_so_far = float((history["close"].fillna(0) * history["volume"].fillna(0)).sum())
    avg_full_day_dollar_volume = _num(symbol_context.get("avg_dollar_volume_20d"))

    quote = quote or Quote(symbol=str(symbol).upper(), fetched_at=datetime.now(timezone.utc))
    bid = _num(quote.bid)
    ask = _num(quote.ask)
    mid = (bid + ask) / 2 if bid is not None and ask is not None and (bid + ask) else last_close
    spread_bps = ((ask - bid) / mid * 10_000) if bid is not None and ask is not None and mid else None
    quote_ts = quote.quote_ts
    if quote_ts and quote_ts.tzinfo is None:
        quote_ts = quote_ts.replace(tzinfo=timezone.utc)
    quote_age = int((_utc(latest_allowed) - pd.Timestamp(quote_ts).tz_convert("UTC")).total_seconds()) if quote_ts else None

    if history["vwap"].notna().any():
        vwap = _num(history["vwap"].dropna().iloc[-1])
    else:
        volume_sum = float(history["volume"].fillna(0).sum())
        vwap = float((history["close"].fillna(0) * history["volume"].fillna(0)).sum() / volume_sum) if volume_sum else None
    high = _num(history["high"].max())
    low = _num(history["low"].min())
    open_price = _num(history.iloc[0].get("open"))
    prior_close = _num(symbol_context.get("prior_day_close"))
    close = last_close

    direction = 1 if (return_5m or 0) > 0 else -1 if (return_5m or 0) < 0 else 0
    seconds_since_signal = _seconds_since_signal_first_fired(
        history,
        latest_allowed,
        direction=direction,
        avg_volume_by_bar=avg_volume_by_bar,
    )
    atr_window = history.tail(12)
    true_ranges = []
    previous_close = None
    for _, row in atr_window.iterrows():
        row_high = _num(row.get("high"))
        row_low = _num(row.get("low"))
        row_close = _num(row.get("close"))
        if row_high is not None and row_low is not None:
            tr = row_high - row_low
            if previous_close is not None:
                tr = max(tr, abs(row_high - previous_close), abs(row_low - previous_close))
            true_ranges.append(tr)
        previous_close = row_close

    market_open = market_context.get("market_open") or market_context.get("open_at")
    market_close = market_context.get("market_close") or market_context.get("close_at")
    market_open_ts = _utc(market_open) if market_open else None
    market_close_ts = _utc(market_close) if market_close else None
    spy_move = _num(market_context.get("spy_intraday_move_pct") or market_context.get("spy_intraday_trend_5m"))
    sector_move = _num(market_context.get("sector_etf_intraday_move_pct") or market_context.get("sector_etf_trend_5m"))

    return {
        "return_5m_pct": return_5m,
        "return_15m_pct": return_15m,
        "return_30m_pct": return_30m,
        "return_60m_pct": return_60m,
        "relative_volume": relative_volume,
        "dollar_volume_15m": dollar_volume_15m,
        "dollar_volume_today_so_far": dollar_volume_today_so_far,
        "liquidity_ratio": _safe_div(dollar_volume_today_so_far, avg_full_day_dollar_volume),
        "spread_bps": spread_bps,
        "spread_bps_zscore_20d": _num(symbol_context.get("spread_bps_zscore_20d")),
        "quote_age_sec": quote_age,
        "vwap_distance_bps_5m": ((close - vwap) / vwap * 10_000) if close is not None and vwap not in {None, 0} else None,
        "intraday_range_position": max(0.0, min(1.0, (close - low) / (high - low))) if close is not None and high is not None and low is not None and high != low else 0.5,
        "pullback_from_high_pct": ((high - close) / high * 100) if close is not None and high not in {None, 0} else None,
        "pullback_from_low_pct": ((close - low) / low * 100) if close is not None and low not in {None, 0} else None,
        "consecutive_5m_bars_in_direction": _consecutive_same_direction(history, direction) if direction else 0,
        "range_open_to_now_pct": ((high - low) / open_price * 100) if high is not None and low is not None and open_price not in {None, 0} else None,
        "gap_pct_from_prior_close": ((open_price - prior_close) / prior_close * 100) if open_price is not None and prior_close not in {None, 0} else None,
        "gap_direction": 1 if open_price is not None and prior_close is not None and open_price > prior_close else -1 if open_price is not None and prior_close is not None and open_price < prior_close else 0,
        "seconds_to_open": (decision - market_open_ts).total_seconds() if market_open_ts else None,
        "seconds_to_close": (market_close_ts - decision).total_seconds() if market_close_ts else None,
        "is_first_15_min": bool(market_open_ts and (decision - market_open_ts).total_seconds() < 900),
        "is_last_30_min": bool(market_close_ts and (market_close_ts - decision).total_seconds() < 1800),
        "time_of_day_bucket": time_of_day_bucket(decision),
        "seconds_since_signal_first_fired": seconds_since_signal,
        "spy_intraday_move_pct": spy_move,
        "sector_etf_intraday_move_pct": sector_move,
        "market_aligned": bool(spy_move is not None and return_30m is not None and (spy_move == 0 or return_30m == 0 or (spy_move > 0) == (return_30m > 0))),
        "sector_aligned": bool(sector_move is not None and return_30m is not None and (sector_move == 0 or return_30m == 0 or (sector_move > 0) == (return_30m > 0))),
        "prior_day_relative_volume": _num(symbol_context.get("prior_day_relative_volume")),
        "volatility_20d": _num(symbol_context.get("volatility_20d")),
        "atr_5m": float(sum(true_ranges) / len(true_ranges)) if true_ranges else None,
        "earnings_today": bool(symbol_context.get("earnings_today", False)),
        "earnings_yesterday": bool(symbol_context.get("earnings_yesterday", False)),
        "news_catalyst_present": bool(symbol_context.get("news_catalyst_present", False)),
        "is_halted": bool(symbol_context.get("is_halted", False)),
        "short_interest_pct": _num(symbol_context.get("short_interest_pct")),
        "borrow_available": bool(symbol_context.get("borrow_available", True)),
    }
