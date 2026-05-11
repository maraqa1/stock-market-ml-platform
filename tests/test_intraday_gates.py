from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from stockml.intraday.block_reasons import BLOCK_REASON_VALUES, BlockReason, coerce_block_reason
from stockml.intraday.features import Bar, IntradayFeatures, NightlySignal, Quote, compute_features
from stockml.intraday.gates import GATE_VERSION, evaluate_gate, next_five_minute_boundary
from stockml.safety.paper_only_guard import LiveTradingDisabledError, paper_only_guard


NOW = datetime(2026, 5, 11, 14, 22, 31, tzinfo=timezone.utc)


def base_features(**overrides):
    data = dict(
        trend_1m=0.001,
        trend_5m=0.004,
        trend_15m=0.006,
        volume_ratio=1.2,
        dollar_volume_today=500_000,
        liquidity_ratio=0.20,
        spread_bps=8,
        spread_bps_zscore_20d=0.4,
        bid_ask_size_imbalance=0.1,
        quote_age_seconds=1.0,
        provider_divergence_pct=None,
        distance_from_vwap_bps=20,
        intraday_range_position=0.65,
        realized_vol_60m_bps=30,
        seconds_to_open=-3600,
        seconds_to_close=5 * 3600,
        spy_intraday_trend_5m=0.001,
        sector_concurrent_move=False,
        consecutive_blocks_today_for_symbol=0,
        last_decision_for_symbol_at=None,
        has_open_position=False,
        has_earnings_today=False,
        has_earnings_after_close=False,
        has_corporate_action_today=False,
        is_halted=False,
        is_first_15_min=False,
        is_last_30_min=False,
        vix_regime="normal",
        decided_at=NOW,
        preserved_bias="long",
    )
    data.update(overrides)
    return IntradayFeatures(**data)


def assert_block(features, expected):
    decision = evaluate_gate(features, NightlySignal("TSLA", "long"))
    assert decision.verdict == "block"
    assert decision.block_reason == expected
    assert decision.gate_version == GATE_VERSION


def test_block_reason_enum_has_fixed_values():
    assert "wide_spread" in BLOCK_REASON_VALUES
    assert "live_disabled" in BLOCK_REASON_VALUES
    assert coerce_block_reason("not-a-real-reason") == BlockReason.MISC


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"is_halted": True}, BlockReason.HALTED),
        ({"has_corporate_action_today": True}, BlockReason.CORPORATE_ACTION),
        ({"has_earnings_today": True}, BlockReason.EARNINGS_TODAY),
        ({"has_earnings_after_close": True, "seconds_to_close": 3 * 3600}, BlockReason.EARNINGS_AFTER_CLOSE),
        ({"quote_age_seconds": 3}, BlockReason.STALE_QUOTE),
        ({"is_first_15_min": True}, BlockReason.NEAR_OPEN),
        ({"is_last_30_min": True}, BlockReason.NEAR_CLOSE),
        ({"spread_bps": 30}, BlockReason.WIDE_SPREAD),
        ({"spread_bps_zscore_20d": 4}, BlockReason.WIDE_SPREAD),
        ({"liquidity_ratio": 0.04}, BlockReason.LOW_LIQUIDITY),
        ({"provider_divergence_pct": 0.006}, BlockReason.PROVIDER_DIVERGENCE),
        ({"consecutive_blocks_today_for_symbol": 3}, BlockReason.SYMBOL_COOLOFF),
        ({"last_decision_for_symbol_at": NOW - timedelta(minutes=30)}, BlockReason.SYMBOL_COOLOFF),
    ],
)
def test_prechecks_fire_expected_block_reason(kwargs, reason):
    assert_block(base_features(**kwargs), reason)


def test_nightly_signal_dropped_when_missing():
    decision = evaluate_gate(base_features(), None)
    assert decision.verdict == "block"
    assert decision.block_reason == BlockReason.NIGHTLY_SIGNAL_DROPPED


def test_nightly_signal_dropped_when_bias_flips():
    decision = evaluate_gate(base_features(preserved_bias="long"), NightlySignal("TSLA", "short"))
    assert decision.verdict == "block"
    assert decision.block_reason == BlockReason.NIGHTLY_SIGNAL_DROPPED


def test_provider_reference_none_does_not_block():
    decision = evaluate_gate(base_features(provider_divergence_pct=None), NightlySignal("TSLA", "long"))
    assert decision.verdict == "allow_long"
    assert decision.block_reason is None


def test_extreme_vix_blocks_as_regime_block():
    assert_block(base_features(vix_regime="extreme"), BlockReason.REGIME_BLOCK)


def test_sector_concentration_blocks_without_open_position():
    assert_block(base_features(sector_concurrent_move=True, has_open_position=False), BlockReason.SECTOR_CONCENTRATION)


def test_sector_concentration_does_not_block_existing_position():
    decision = evaluate_gate(base_features(sector_concurrent_move=True, has_open_position=True), NightlySignal("TSLA", "long"))
    assert decision.verdict == "allow_long"


def test_long_confirmation_all_conditions_pass():
    decision = evaluate_gate(base_features(), {"bias": "Long", "preserved_bias": "long"})
    assert decision.verdict == "allow_long"
    assert decision.block_reason is None
    assert "trend_5m_positive" in decision.contributing


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trend_5m": -0.001},
        {"trend_15m": -0.001},
        {"distance_from_vwap_bps": -60},
        {"intraday_range_position": 0.3},
        {"bid_ask_size_imbalance": -0.3},
        {"spy_intraday_trend_5m": -0.006},
    ],
)
def test_long_confirmation_failure_returns_hold_not_block(kwargs):
    decision = evaluate_gate(base_features(**kwargs), NightlySignal("TSLA", "long"))
    assert decision.verdict == "hold"
    assert decision.block_reason is None
    assert decision.contributing


def short_features(**overrides):
    data = dict(
        trend_1m=-0.001,
        trend_5m=-0.004,
        trend_15m=-0.006,
        bid_ask_size_imbalance=-0.1,
        distance_from_vwap_bps=-20,
        intraday_range_position=0.35,
        spy_intraday_trend_5m=-0.001,
        preserved_bias="short",
    )
    data.update(overrides)
    return base_features(**data)


def test_short_confirmation_all_conditions_pass():
    decision = evaluate_gate(short_features(), NightlySignal("TSLA", "short"))
    assert decision.verdict == "allow_short"
    assert decision.block_reason is None
    assert "trend_5m_negative" in decision.contributing


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trend_5m": 0.001},
        {"trend_15m": 0.001},
        {"distance_from_vwap_bps": 60},
        {"intraday_range_position": 0.7},
        {"bid_ask_size_imbalance": 0.3},
        {"spy_intraday_trend_5m": 0.006},
    ],
)
def test_short_confirmation_failure_returns_hold_not_block(kwargs):
    decision = evaluate_gate(short_features(**kwargs), NightlySignal("TSLA", "short"))
    assert decision.verdict == "hold"
    assert decision.block_reason is None
    assert decision.contributing


def test_gate_is_pure_for_same_inputs():
    features = base_features()
    assert evaluate_gate(features, NightlySignal("TSLA", "long")) == evaluate_gate(features, NightlySignal("TSLA", "long"))


def test_valid_until_falls_on_next_five_minute_boundary():
    assert next_five_minute_boundary(NOW) == datetime(2026, 5, 11, 14, 25, tzinfo=timezone.utc)
    decision = evaluate_gate(base_features(), NightlySignal("TSLA", "long"))
    assert decision.valid_until.minute % 5 == 0
    assert decision.valid_until.second == 0


def test_compute_features_is_deterministic_and_tolerates_missing_values():
    bars = [Bar(open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1000 + i, vwap=100 + i) for i in range(12)]
    quote = Quote("TSLA", bid=111.9, ask=112.1, bid_size=120, ask_size=100, last_price=112, quote_ts=NOW - timedelta(seconds=1), fetched_at=NOW)
    ctx = {"open_at": NOW - timedelta(hours=1), "close_at": NOW + timedelta(hours=5), "spy_intraday_trend_5m": 0.001}
    pos = {"avg_dollar_volume_20d": 10_000_000, "prior_close": 99, "day_open": 100}

    first = compute_features("TSLA", quote, bars, ctx, {"preserved_bias": "long"}, pos)
    second = compute_features("TSLA", quote, bars, ctx, {"preserved_bias": "long"}, pos)

    assert first == second
    assert first.spread_bps == pytest.approx(17.8571, rel=1e-4)
    assert first.quote_age_seconds == 1
    assert first.gap_direction == 1


def test_paper_only_guard_blocks_live_mode():
    assert paper_only_guard(mode="paper")
    with pytest.raises(LiveTradingDisabledError):
        paper_only_guard(mode="live")
