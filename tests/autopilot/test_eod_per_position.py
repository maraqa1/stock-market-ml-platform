from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from stockml.autopilot.eod import EODConfig, run_eod_tick, tag_dispositions


ET = ZoneInfo("America/New_York")


def test_same_day_position_flattens_at_eod():
    positions = pd.DataFrame(
        [
            {
                "symbol": "DAY",
                "strategy_stream": "same_day_momentum",
                "must_flatten_at_eod": True,
                "unrealized_plpc": 0.03,
                "age_days": 0,
            }
        ]
    )
    calls = []

    result = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=True),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert result["eod_state"] == "flatten"
    assert result["eod_flatten_submitted"] == 1
    assert calls == [("DAY", "close")]
    assert result["eod_dispositions"][0]["strategy_stream"] == "same_day_momentum"


def test_multi_day_position_does_not_flatten_at_eod():
    positions = pd.DataFrame(
        [
            {
                "symbol": "SWING",
                "strategy_stream": "multi_day_forecast",
                "must_flatten_at_eod": False,
                "unrealized_plpc": 0.04,
                "age_days": 1,
            }
        ]
    )
    calls = []

    result = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=False),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert result["eod_state"] == "flatten"
    assert result["eod_flatten_submitted"] == 0
    assert calls == []


def test_multi_day_position_trims_when_stale_or_weak():
    positions = pd.DataFrame(
        [
            {
                "symbol": "RISK",
                "strategy_stream": "multi_day_forecast",
                "must_flatten_at_eod": False,
                "unrealized_plpc": -0.02,
                "age_days": 2,
            }
        ]
    )
    calls = []

    result = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 47, tzinfo=ET),
        state={},
        config=EODConfig(),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert result["eod_state"] == "trim"
    assert result["eod_flatten_submitted"] == 1
    assert calls == [("RISK", "close")]


def test_max_hold_until_enforcement():
    positions = pd.DataFrame(
        [
            {
                "symbol": "AGED",
                "strategy_stream": "multi_day_forecast",
                "must_flatten_at_eod": False,
                "max_hold_until": "2026-05-11",
                "unrealized_plpc": 0.05,
                "age_days": 1,
            }
        ]
    )
    calls = []

    result = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=True),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert result["eod_flatten_submitted"] == 1
    assert result["eod_dispositions"][0]["reason"] == "max_hold_until_exceeded"
    assert calls == [("AGED", "close")]


def test_legacy_trading_stream_maps_to_strategy_stream():
    rows = tag_dispositions(
        pd.DataFrame([{"symbol": "DAY", "trading_stream": "same_day", "unrealized_plpc": 0.01}]),
        config=EODConfig(),
        now=datetime(2026, 5, 12, 12, 0, tzinfo=ET),
    )

    assert rows[0]["strategy_stream"] == "same_day_momentum"
    assert rows[0]["must_flatten_at_eod"] is True
