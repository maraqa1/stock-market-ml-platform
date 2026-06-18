from __future__ import annotations

from datetime import datetime, timezone

from stockml.trading.session_mode import classify_session_mode


def test_regular_market_time_classifies_regular_session():
    assert classify_session_mode(datetime(2026, 6, 18, 14, 0, tzinfo=timezone.utc)) == "regular_session"


def test_premarket_time_classifies_pre_market():
    assert classify_session_mode(datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)) == "pre_market"


def test_after_hours_time_classifies_after_hours():
    assert classify_session_mode(datetime(2026, 6, 18, 21, 0, tzinfo=timezone.utc)) == "after_hours"


def test_overnight_time_classifies_overnight_24_5():
    assert classify_session_mode(datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)) == "overnight_24_5"


def test_weekend_classifies_weekend_closed():
    assert classify_session_mode(datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)) == "weekend_closed"
