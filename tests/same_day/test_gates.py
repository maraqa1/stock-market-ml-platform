from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stockml.intraday import kill_switch
from stockml.same_day.gates import SameDayGateConfig, evaluate, load_config


NOW = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)


def _features(**overrides):
    values = {
        "avg_dollar_volume_20d": 30_000_000,
        "last_price": 25,
        "market_cap": 800_000_000,
        "spread_bps": 5,
        "spread_bps_zscore_20d": 0.5,
        "is_first_15_min": False,
        "is_last_30_min": False,
        "seconds_since_signal_first_fired": 600,
        "is_halted": False,
        "earnings_today": False,
        "earnings_yesterday": False,
        "seconds_to_open": 9000,
        "market_aligned": True,
        "sector_aligned": True,
        "sector_etf_intraday_move_pct": 0.1,
        "borrow_available": True,
    }
    values.update(overrides)
    return values


def allow_gate(**kwargs):
    return kill_switch.KillSwitchVerdict(True, [], NOW, False, None, None)


def block_gate(**kwargs):
    return kill_switch.KillSwitchVerdict(False, ["daily.loss"], NOW, True, None, None)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"avg_dollar_volume_20d": 1_000_000}, "REJECTED_LIQUIDITY_THIN"),
        ({"last_price": 2}, "REJECTED_PRICE_BAND"),
        ({"market_cap": 100_000_000}, "REJECTED_MARKETCAP_MIN"),
        ({"spread_bps": 20}, "REJECTED_WIDE_SPREAD"),
        ({"is_first_15_min": True}, "REJECTED_TIME_OF_DAY"),
        ({"seconds_since_signal_first_fired": 60}, "REJECTED_SIGNAL_FRESH"),
        ({"seconds_since_signal_first_fired": 7200}, "REJECTED_SIGNAL_STALE"),
        ({"is_halted": True}, "REJECTED_HALTED"),
        ({"earnings_today": True}, "REJECTED_EARNINGS_TODAY"),
        ({"earnings_yesterday": True, "seconds_to_open": 3600}, "REJECTED_EARNINGS_RECENT"),
        ({"market_aligned": False}, "REJECTED_MARKET_MISALIGNED"),
        ({"sector_aligned": False, "sector_etf_intraday_move_pct": 1.2}, "REJECTED_SECTOR_MISALIGNED"),
        ({"borrow_available": False}, "REJECTED_NO_BORROW"),
    ],
)
def test_each_gate_rejects_correctly(overrides, reason):
    result = evaluate(
        _features(**overrides),
        direction="short" if reason == "REJECTED_NO_BORROW" else "long",
        continuation_probability=0.70,
        reversal_probability=0.20,
        config=SameDayGateConfig(),
        kill_switch_gate=allow_gate,
    )

    assert result.passed is False
    assert result.reason == reason


def test_continuation_and_reversal_probability_gates():
    low_continuation = evaluate(_features(), direction="long", continuation_probability=0.55, reversal_probability=0.20, config=SameDayGateConfig(), kill_switch_gate=allow_gate)
    high_reversal = evaluate(_features(), direction="long", continuation_probability=0.70, reversal_probability=0.40, config=SameDayGateConfig(), kill_switch_gate=allow_gate)

    assert low_continuation.reason == "REJECTED_CONTINUATION_THRESHOLD"
    assert high_reversal.reason == "REJECTED_REVERSAL_RISK_TOO_HIGH"


def test_wide_spread_passes_when_expected_edge_covers_cost():
    result = evaluate(
        _features(spread_bps=40, expected_move_bps=250),
        direction="long",
        continuation_probability=0.70,
        reversal_probability=0.20,
        config=SameDayGateConfig(max_spread_bps=15, estimated_cost_bps=10, min_edge_to_spread_ratio=3.0, min_expected_net_edge_bps=25),
        kill_switch_gate=allow_gate,
    )

    assert result.passed is True


def test_wide_spread_rejects_when_expected_edge_is_weak():
    result = evaluate(
        _features(spread_bps=40, expected_move_bps=80),
        direction="long",
        continuation_probability=0.70,
        reversal_probability=0.20,
        config=SameDayGateConfig(max_spread_bps=15, estimated_cost_bps=10, min_edge_to_spread_ratio=3.0, min_expected_net_edge_bps=25),
        kill_switch_gate=allow_gate,
    )

    assert result.passed is False
    assert result.reason == "REJECTED_WIDE_SPREAD"
    assert result.details["spread_gate_decision"] == "wide_spread_edge_insufficient"


def test_symbol_activity_and_daily_cap_gates():
    symbol = evaluate(_features(), direction="long", continuation_probability=0.70, reversal_probability=0.20, config=SameDayGateConfig(), same_day_attempts_today_for_symbol=3, kill_switch_gate=allow_gate)
    daily = evaluate(_features(), direction="long", continuation_probability=0.70, reversal_probability=0.20, config=SameDayGateConfig(), same_day_candidates_today_count=20, kill_switch_gate=allow_gate)

    assert symbol.reason == "REJECTED_SYMBOL_ACTIVITY_LIMIT"
    assert daily.reason == "REJECTED_DAILY_CANDIDATE_CAP"


def test_gate_order_short_circuits():
    result = evaluate(
        _features(avg_dollar_volume_20d=1, last_price=2),
        direction="long",
        continuation_probability=0.50,
        reversal_probability=0.60,
        config=SameDayGateConfig(),
        kill_switch_gate=allow_gate,
    )

    assert result.gate == "liquidity"
    assert result.reason == "REJECTED_LIQUIDITY_THIN"


def test_kill_switch_gate_rejects_after_candidate_quality_passes():
    result = evaluate(
        _features(),
        direction="long",
        continuation_probability=0.70,
        reversal_probability=0.20,
        config=SameDayGateConfig(),
        kill_switch_gate=block_gate,
    )

    assert result.reason == "BLOCKED_KILL_SWITCH"
    assert result.details == {"tripped": ["daily.loss"]}


def test_gate_config_loads_defaults_from_yaml():
    cfg = load_config()

    assert cfg.min_avg_dollar_volume_20d == 20_000_000
    assert cfg.min_continuation_probability == 0.60
