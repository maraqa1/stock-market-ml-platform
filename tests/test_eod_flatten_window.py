from datetime import datetime, timezone

import pandas as pd

from stockml.autopilot.eod import EODConfig, EODFlattenWindowConfig, eod_flatten_window_active, run_eod_tick


def _positions():
    return pd.DataFrame([{"symbol": "AAA", "strategy_stream": "same_day_momentum", "qty": 1}])


def test_eod_flatten_blocked_outside_configured_window():
    now = datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)  # 14:00 NY
    assert eod_flatten_window_active(now, EODFlattenWindowConfig()) is False
    submitted = []
    result = run_eod_tick(
        _positions(),
        now=now,
        state={},
        config=EODConfig(t_minus_5_min=5, market_close_time_local="14:04"),
        close_func=lambda symbol, reason: submitted.append(symbol) or {"status": "submitted"},
        flatten_window_config=EODFlattenWindowConfig(),
    )
    assert result["eod_flatten_submitted"] == 0
    assert submitted == []
    assert "eod_flatten_outside_window" in result["eod_action_notes"]


def test_eod_flatten_allowed_inside_configured_window():
    now = datetime(2026, 7, 10, 19, 56, tzinfo=timezone.utc)  # 15:56 NY
    assert eod_flatten_window_active(now, EODFlattenWindowConfig()) is True
    submitted = []
    result = run_eod_tick(
        _positions(),
        now=now,
        state={},
        config=EODConfig(t_minus_5_min=5, market_close_time_local="16:00"),
        close_func=lambda symbol, reason: submitted.append((symbol, reason)) or {"status": "submitted"},
        flatten_window_config=EODFlattenWindowConfig(),
    )
    assert result["eod_flatten_submitted"] == 1
    assert submitted == [("AAA", "close")]
