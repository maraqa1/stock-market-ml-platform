from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, NamedTuple

from stockml.intraday.block_reasons import BlockReason
from stockml.intraday.features import IntradayFeatures, NightlySignal


GATE_VERSION = "v1.0.0"


class GateDecision(NamedTuple):
    verdict: Literal["allow_long", "allow_short", "hold", "block"]
    block_reason: BlockReason | None
    valid_until: datetime
    gate_version: str
    contributing: list[str]


def _aware(value: datetime | None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def next_five_minute_boundary(now: datetime | None = None) -> datetime:
    current = _aware(now)
    minute = (current.minute // 5 + 1) * 5
    if minute >= 60:
        boundary = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        boundary = current.replace(minute=minute, second=0, microsecond=0)
    return boundary


def _decision(
    features: IntradayFeatures,
    verdict: Literal["allow_long", "allow_short", "hold", "block"],
    reason: BlockReason | None = None,
    contributing: list[str] | None = None,
) -> GateDecision:
    return GateDecision(
        verdict=verdict,
        block_reason=reason,
        valid_until=next_five_minute_boundary(features.decided_at),
        gate_version=GATE_VERSION,
        contributing=list(contributing or []),
    )


def _block(features: IntradayFeatures, reason: BlockReason, rule_name: str) -> GateDecision:
    return _decision(features, "block", reason, [rule_name])


def _nightly_bias(nightly_signal: NightlySignal | dict | None) -> str | None:
    if nightly_signal is None:
        return None
    if isinstance(nightly_signal, NightlySignal):
        return nightly_signal.normalized_bias
    raw = str(nightly_signal.get("bias") or nightly_signal.get("trade_action") or nightly_signal.get("side") or "").lower()
    if raw in {"long", "buy"}:
        return "long"
    if raw in {"short", "sell"}:
        return "short"
    return raw or None


def _preserved_bias(features: IntradayFeatures, nightly_signal: NightlySignal | dict | None) -> str | None:
    raw = features.preserved_bias
    if raw is None and isinstance(nightly_signal, dict):
        raw = nightly_signal.get("preserved_bias")
    raw = str(raw or "").lower()
    if raw in {"long", "buy"}:
        return "long"
    if raw in {"short", "sell"}:
        return "short"
    return raw or None


def _recent_decision(features: IntradayFeatures) -> bool:
    if features.last_decision_for_symbol_at is None:
        return False
    decided_at = _aware(features.decided_at)
    last_decision = _aware(features.last_decision_for_symbol_at)
    return decided_at - last_decision < timedelta(minutes=60)


def _gt(value: float | None, threshold: float) -> bool:
    return value is not None and value > threshold


def _lt(value: float | None, threshold: float) -> bool:
    return value is not None and value < threshold


def _provider_diverged(features: IntradayFeatures) -> bool:
    return features.provider_divergence_pct is not None and abs(features.provider_divergence_pct) > 0.005


def evaluate_gate(features: IntradayFeatures, nightly_signal: NightlySignal | dict | None) -> GateDecision:
    """Evaluate the deterministic v1 intraday confirmation waterfall."""
    if features.is_halted:
        return _block(features, BlockReason.HALTED, "halted")
    if features.has_corporate_action_today:
        return _block(features, BlockReason.CORPORATE_ACTION, "corporate_action_today")
    if features.has_earnings_today:
        return _block(features, BlockReason.EARNINGS_TODAY, "earnings_today")
    if features.has_earnings_after_close and features.seconds_to_close is not None and features.seconds_to_close < 4 * 3600:
        return _block(features, BlockReason.EARNINGS_AFTER_CLOSE, "earnings_after_close")
    if features.quote_age_seconds is not None and features.quote_age_seconds > 2:
        return _block(features, BlockReason.STALE_QUOTE, "stale_quote")
    if features.is_first_15_min:
        return _block(features, BlockReason.NEAR_OPEN, "near_open")
    if features.is_last_30_min:
        return _block(features, BlockReason.NEAR_CLOSE, "near_close")
    if _gt(features.spread_bps, 25) or _gt(features.spread_bps_zscore_20d, 3):
        return _block(features, BlockReason.WIDE_SPREAD, "wide_spread")
    if _lt(features.liquidity_ratio, 0.05):
        return _block(features, BlockReason.LOW_LIQUIDITY, "low_liquidity")
    if _provider_diverged(features):
        return _block(features, BlockReason.PROVIDER_DIVERGENCE, "provider_divergence")
    if features.consecutive_blocks_today_for_symbol >= 3:
        return _block(features, BlockReason.SYMBOL_COOLOFF, "three_blocks_today")
    if _recent_decision(features):
        return _block(features, BlockReason.SYMBOL_COOLOFF, "decision_within_60m")

    bias = _nightly_bias(nightly_signal)
    preserved = _preserved_bias(features, nightly_signal)
    if bias is None or (preserved is not None and bias != preserved):
        return _block(features, BlockReason.NIGHTLY_SIGNAL_DROPPED, "nightly_signal_dropped")

    if features.vix_regime == "extreme":
        return _block(features, BlockReason.REGIME_BLOCK, "vix_extreme")
    if features.sector_concurrent_move and not features.has_open_position:
        return _block(features, BlockReason.SECTOR_CONCENTRATION, "sector_concurrent_move")

    if bias == "long":
        required = [
            ("trend_5m_positive", _gt(features.trend_5m, 0)),
            ("trend_15m_positive", _gt(features.trend_15m, 0)),
            ("not_far_below_vwap", features.distance_from_vwap_bps is not None and features.distance_from_vwap_bps > -50),
            ("upper_intraday_range", _gt(features.intraday_range_position, 0.4)),
            ("book_not_heavily_offered", features.bid_ask_size_imbalance is not None and features.bid_ask_size_imbalance > -0.2),
            ("market_not_collapsing", features.spy_intraday_trend_5m is not None and features.spy_intraday_trend_5m > -0.005),
        ]
        if all(passed for _, passed in required):
            return _decision(features, "allow_long", contributing=[name for name, _ in required])
        return _decision(features, "hold", contributing=[name for name, passed in required if not passed])

    if bias == "short":
        required = [
            ("trend_5m_negative", _lt(features.trend_5m, 0)),
            ("trend_15m_negative", _lt(features.trend_15m, 0)),
            ("not_far_above_vwap", features.distance_from_vwap_bps is not None and features.distance_from_vwap_bps < 50),
            ("lower_intraday_range", features.intraday_range_position is not None and features.intraday_range_position < 0.6),
            ("book_not_heavily_bid", features.bid_ask_size_imbalance is not None and features.bid_ask_size_imbalance < 0.2),
            ("market_not_ripping", features.spy_intraday_trend_5m is not None and features.spy_intraday_trend_5m < 0.005),
        ]
        if all(passed for _, passed in required):
            return _decision(features, "allow_short", contributing=[name for name, _ in required])
        return _decision(features, "hold", contributing=[name for name, passed in required if not passed])

    return _decision(features, "hold", contributing=["unsupported_bias"])

