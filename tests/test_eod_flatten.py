from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from stockml.autopilot.eod import EODConfig, banner_for_state, eod_state, run_eod_tick, tag_dispositions


ET = ZoneInfo("America/New_York")


def test_eod_state_windows_full_day_and_half_day():
    cfg = EODConfig(market_close_time_local="16:00")
    assert eod_state(datetime(2026, 5, 12, 15, 29, tzinfo=ET), cfg) == "inactive"
    assert eod_state(datetime(2026, 5, 12, 15, 35, tzinfo=ET), cfg) == "review"
    assert eod_state(datetime(2026, 5, 12, 15, 47, tzinfo=ET), cfg) == "trim"
    assert eod_state(datetime(2026, 5, 12, 15, 52, tzinfo=ET), cfg) == "observe"
    assert eod_state(datetime(2026, 5, 12, 15, 56, tzinfo=ET), cfg) == "flatten"
    assert eod_state(datetime(2026, 5, 12, 15, 59, tzinfo=ET), cfg) == "verify"
    assert eod_state(datetime(2026, 5, 12, 16, 0, tzinfo=ET), cfg) == "postclose"

    half_day = EODConfig(market_close_time_local="13:00")
    assert eod_state(datetime(2026, 5, 12, 12, 35, tzinfo=ET), half_day) == "review"


def test_tag_dispositions_for_weak_stale_winner_and_none():
    positions = pd.DataFrame(
        [
            {"symbol": "WEAK", "unrealized_plpc": -0.02, "age_days": 2},
            {"symbol": "STALE", "unrealized_plpc": 0.0, "age_days": 25},
            {"symbol": "WIN", "unrealized_plpc": 0.03, "age_days": 1},
            {"symbol": "OK", "unrealized_plpc": 0.0, "age_days": 1},
        ]
    )

    rows = tag_dispositions(positions, config=EODConfig())
    by_symbol = {row["symbol"]: row["disposition"] for row in rows}

    assert by_symbol["WEAK"] == "weak"
    assert by_symbol["STALE"] == "stale"
    assert by_symbol["WIN"] == "winner_hold"
    assert by_symbol["OK"] == "none"


def test_t_minus_15_trim_closes_only_weak_and_stale_positions():
    positions = pd.DataFrame(
        [
            {"symbol": "WEAK", "unrealized_plpc": -0.02, "age_days": 2},
            {"symbol": "STALE", "unrealized_plpc": 0.0, "age_days": 25},
            {"symbol": "WIN", "unrealized_plpc": 0.03, "age_days": 1},
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
    assert result["eod_flatten_submitted"] == 2
    assert calls == [("WEAK", "close"), ("STALE", "close")]
    assert result["eod_banner"] == "EOD trim: closing 2 weak/stale positions."


def test_t_minus_5_flatten_closes_winners_by_default_and_honors_holdover_toggle():
    positions = pd.DataFrame([{"symbol": "WIN", "unrealized_plpc": 0.03, "age_days": 1}])
    calls = []

    result = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=False),
        close_func=lambda symbol, action: calls.append(symbol) or {"status": "submitted", "message": "ok"},
    )

    assert result["eod_state"] == "flatten"
    assert result["eod_flatten_submitted"] == 1
    assert calls == ["WIN"]

    holdover = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=True),
        close_func=lambda symbol, action: {"status": "submitted", "message": "ok"},
    )

    assert holdover["eod_flatten_submitted"] == 0
    assert holdover["eod_holdover_allowed"] is True


def test_verify_rescue_flattens_remaining_positions_before_close():
    positions = pd.DataFrame([{"symbol": "ANGI", "unrealized_plpc": 0.0, "age_days": 0}])
    calls = []

    verify = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 59, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=False),
        close_func=lambda symbol, action: calls.append(("verify", symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert verify["eod_state"] == "verify"
    assert verify["eod_flatten_submitted"] == 1
    assert verify["eod_banner"] == "EOD verify: closing 1 positions still open."
    assert calls == [("verify", "ANGI", "close")]


def test_postclose_does_not_queue_regular_market_close_by_default():
    positions = pd.DataFrame([{"symbol": "ANGI", "unrealized_plpc": 0.0, "age_days": 0}])
    calls = []

    postclose = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 16, 1, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=False),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert postclose["eod_state"] == "postclose"
    assert postclose["eod_flatten_submitted"] == 0
    assert postclose["eod_banner"] == "Held overnight: 1 positions did not flatten."
    assert calls == []


def test_postclose_rescue_orders_require_explicit_opt_in():
    positions = pd.DataFrame([{"symbol": "ANGI", "unrealized_plpc": 0.0, "age_days": 0}])
    calls = []

    postclose = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 16, 1, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=False, submit_postclose_rescue_orders=True),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert postclose["eod_state"] == "postclose"
    assert postclose["eod_flatten_submitted"] == 1
    assert postclose["eod_banner"] == "Post-close rescue flatten: closing 1 remaining positions."
    assert calls == [("ANGI", "close")]


def test_eod_skips_new_closes_when_orders_are_already_in_flight():
    positions = pd.DataFrame([{"symbol": "WEAK", "unrealized_plpc": -0.02, "age_days": 2}])

    result = run_eod_tick(
        positions,
        now=datetime(2026, 5, 12, 15, 47, tzinfo=ET),
        state={},
        config=EODConfig(),
        open_orders=1,
        close_func=lambda symbol, action: {"status": "submitted", "message": "should_not_run"},
    )

    assert result["eod_actions"] == 0
    assert result["eod_flatten_submitted"] == 0
    assert "skipped:open_orders_in_flight" in result["eod_action_notes"]


def test_postclose_banner_reports_overnight_positions():
    assert banner_for_state("postclose", remaining_count=2, flattened_count=1) == "Held overnight: 2 positions did not flatten."
