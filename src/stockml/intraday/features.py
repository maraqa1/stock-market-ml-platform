from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    last_price: float | None = None
    last_size: float | None = None
    quote_ts: datetime | None = None
    fetched_at: datetime | None = None
    source: str = "alpaca"


@dataclass(frozen=True)
class Bar:
    close: float | None = None
    high: float | None = None
    low: float | None = None
    open: float | None = None
    volume: float | None = None
    vwap: float | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class NightlySignal:
    symbol: str
    bias: str
    score: float | None = None
    rank: int | None = None

    @property
    def normalized_bias(self) -> str:
        value = str(self.bias or "").strip().lower()
        if value in {"long", "buy"}:
            return "long"
        if value in {"short", "sell"}:
            return "short"
        return value


@dataclass(frozen=True)
class IntradayFeatures:
    # Trend
    trend_1m: float | None = None
    trend_5m: float | None = None
    trend_15m: float | None = None

    # Volume
    volume_ratio: float | None = None
    dollar_volume_today: float | None = None
    liquidity_ratio: float | None = None

    # Microstructure
    mid_price: float | None = None
    spread_bps: float | None = None
    spread_bps_zscore_20d: float | None = None
    bid_ask_size_imbalance: float | None = None
    quote_age_seconds: float | None = None
    provider_divergence_pct: float | None = None

    # Position
    distance_from_vwap_bps: float | None = None
    intraday_range_position: float | None = None
    gap_direction: int | None = None
    gap_magnitude_bps: float | None = None

    # Volatility
    realized_vol_60m_bps: float | None = None
    volatility_burst: bool = False
    atr_5m: float | None = None

    # Time
    seconds_to_open: float | None = None
    seconds_to_close: float | None = None
    is_first_15_min: bool = False
    is_last_30_min: bool = False

    # Context
    vix_today: float | None = None
    vix_regime: str | None = None
    spy_intraday_trend_5m: float | None = None
    sector_etf_trend_5m: float | None = None
    sector_concurrent_move: bool = False

    # Self-state
    consecutive_blocks_today_for_symbol: int = 0
    decisions_today_for_symbol: int = 0
    last_decision_for_symbol_at: datetime | None = None
    has_open_position: bool = False
    has_earnings_today: bool = False
    has_earnings_after_close: bool = False
    has_corporate_action_today: bool = False
    is_halted: bool = False

    decided_at: datetime | None = None
    preserved_bias: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _trend(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    previous = closes[-1 - lookback]
    if previous == 0:
        return None
    return (closes[-1] - previous) / previous


def _atr(bars: list[Bar]) -> float | None:
    ranges = []
    prev_close: float | None = None
    for bar in bars:
        high = _safe_float(bar.high)
        low = _safe_float(bar.low)
        close = _safe_float(bar.close)
        if high is None or low is None:
            prev_close = close
            continue
        true_range = high - low
        if prev_close is not None:
            true_range = max(true_range, abs(high - prev_close), abs(low - prev_close))
        ranges.append(true_range)
        prev_close = close
    return mean(ranges) if ranges else None


def compute_features(
    symbol: str,
    quote: Quote | None,
    bars: list[Bar] | None,
    market_context: dict[str, Any] | None = None,
    nightly_context: dict[str, Any] | None = None,
    position_context: dict[str, Any] | None = None,
) -> IntradayFeatures:
    """Compute deterministic intraday features from supplied snapshots.

    Missing inputs produce ``None`` feature values rather than exceptions.
    """
    market_context = market_context or {}
    nightly_context = nightly_context or {}
    position_context = position_context or {}
    bars = list(bars or [])
    quote = quote or Quote(symbol=symbol)

    now = quote.fetched_at or market_context.get("now") or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    closes = [_safe_float(bar.close) for bar in bars]
    closes = [value for value in closes if value is not None]
    volumes = [_safe_float(bar.volume) for bar in bars]
    volumes = [value for value in volumes if value is not None]
    last_close = closes[-1] if closes else _safe_float(quote.last_price)
    last_volume = volumes[-1] if volumes else None

    bid = _safe_float(quote.bid)
    ask = _safe_float(quote.ask)
    mid = (bid + ask) / 2 if bid is not None and ask is not None and (bid + ask) else last_close
    spread_bps = ((ask - bid) / mid * 10_000) if bid is not None and ask is not None and mid else None

    bid_size = _safe_float(quote.bid_size)
    ask_size = _safe_float(quote.ask_size)
    size_denominator = (bid_size or 0) + (ask_size or 0)
    imbalance = ((bid_size or 0) - (ask_size or 0)) / size_denominator if size_denominator else None

    quote_ts = quote.quote_ts
    if quote_ts and quote_ts.tzinfo is None:
        quote_ts = quote_ts.replace(tzinfo=timezone.utc)
    quote_age = (now - quote_ts).total_seconds() if quote_ts else None

    avg_volume = mean(volumes) if volumes else None
    dollar_volume_today = _safe_float(position_context.get("dollar_volume_today"))
    if dollar_volume_today is None and last_close is not None:
        dollar_volume_today = sum(volume * last_close for volume in volumes)
    avg_dollar_volume_20d = _safe_float(position_context.get("avg_dollar_volume_20d"))

    vwap_today = _safe_float(position_context.get("vwap_today"))
    if vwap_today is None:
        vwaps = [_safe_float(bar.vwap) for bar in bars]
        vwaps = [value for value in vwaps if value is not None]
        vwap_today = vwaps[-1] if vwaps else None

    highs = [_safe_float(bar.high) for bar in bars]
    lows = [_safe_float(bar.low) for bar in bars]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    day_high = _safe_float(position_context.get("day_high")) or (max(highs) if highs else None)
    day_low = _safe_float(position_context.get("day_low")) or (min(lows) if lows else None)
    range_position = None
    if last_close is not None and day_high is not None and day_low is not None and day_high != day_low:
        range_position = (last_close - day_low) / (day_high - day_low)

    prior_close = _safe_float(position_context.get("prior_close"))
    day_open = _safe_float(position_context.get("day_open")) or (_safe_float(bars[0].open) if bars else None)
    gap_direction = None
    gap_magnitude = None
    if prior_close not in {None, 0} and day_open is not None:
        delta = day_open - prior_close
        gap_direction = 1 if delta > 0 else -1 if delta < 0 else 0
        gap_magnitude = abs(delta) / prior_close * 10_000

    returns = []
    for prev, curr in zip(closes, closes[1:]):
        if prev:
            returns.append((curr - prev) / prev)
    realized_vol = pstdev(returns) * 10_000 if len(returns) > 1 else None
    median_5d = _safe_float(position_context.get("realized_vol_60m_bps_median_5d"))

    open_at = market_context.get("open_at")
    close_at = market_context.get("close_at")
    for key, value in {"open_at": open_at, "close_at": close_at}.items():
        if isinstance(value, datetime) and value.tzinfo is None:
            market_context[key] = value.replace(tzinfo=timezone.utc)
    open_at = market_context.get("open_at")
    close_at = market_context.get("close_at")
    seconds_to_open = (open_at - now).total_seconds() if isinstance(open_at, datetime) else None
    seconds_to_close = (close_at - now).total_seconds() if isinstance(close_at, datetime) else None

    return IntradayFeatures(
        trend_1m=_trend(closes, 1),
        trend_5m=_trend(closes, 5),
        trend_15m=_trend(closes, 3),
        volume_ratio=_safe_div(last_volume, avg_volume),
        dollar_volume_today=dollar_volume_today,
        liquidity_ratio=_safe_div(dollar_volume_today, avg_dollar_volume_20d),
        mid_price=mid,
        spread_bps=spread_bps,
        spread_bps_zscore_20d=_safe_float(position_context.get("spread_bps_zscore_20d")),
        bid_ask_size_imbalance=imbalance,
        quote_age_seconds=quote_age,
        provider_divergence_pct=_safe_float(market_context.get("provider_divergence_pct")),
        distance_from_vwap_bps=((last_close - vwap_today) / vwap_today * 10_000) if last_close is not None and vwap_today not in {None, 0} else None,
        intraday_range_position=range_position,
        gap_direction=gap_direction,
        gap_magnitude_bps=gap_magnitude,
        realized_vol_60m_bps=realized_vol,
        volatility_burst=bool(realized_vol is not None and median_5d not in {None, 0} and realized_vol > 2 * median_5d),
        atr_5m=_atr(bars),
        seconds_to_open=seconds_to_open,
        seconds_to_close=seconds_to_close,
        is_first_15_min=bool(market_context.get("is_first_15_min", False)),
        is_last_30_min=bool(market_context.get("is_last_30_min", False)),
        vix_today=_safe_float(market_context.get("vix_today")),
        vix_regime=market_context.get("vix_regime"),
        spy_intraday_trend_5m=_safe_float(market_context.get("spy_intraday_trend_5m")),
        sector_etf_trend_5m=_safe_float(market_context.get("sector_etf_trend_5m")),
        sector_concurrent_move=bool(market_context.get("sector_concurrent_move", False)),
        consecutive_blocks_today_for_symbol=int(position_context.get("consecutive_blocks_today_for_symbol") or 0),
        decisions_today_for_symbol=int(position_context.get("decisions_today_for_symbol") or 0),
        last_decision_for_symbol_at=position_context.get("last_decision_for_symbol_at"),
        has_open_position=bool(position_context.get("has_open_position", False)),
        has_earnings_today=bool(position_context.get("has_earnings_today", False)),
        has_earnings_after_close=bool(position_context.get("has_earnings_after_close", False)),
        has_corporate_action_today=bool(position_context.get("has_corporate_action_today", False)),
        is_halted=bool(position_context.get("is_halted", False)),
        decided_at=now,
        preserved_bias=nightly_context.get("preserved_bias"),
        extra={"symbol": symbol},
    )
