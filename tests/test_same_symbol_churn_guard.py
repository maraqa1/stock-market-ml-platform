from datetime import datetime, timedelta, timezone

from stockml.trading.anti_churn_guard import AntiChurnConfig, guard_actions


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)


def test_same_symbol_reopen_within_cooldown_is_blocked():
    actions = [{"symbol": "DFTX", "action": "open", "side": "buy"}]
    history = [{"symbol": "DFTX", "closed_at": (NOW - timedelta(minutes=10)).isoformat(), "direction": "long", "action": "close"}]
    allowed, report = guard_actions(actions, trade_history=history, now=NOW, config=AntiChurnConfig(max_opens_per_symbol_per_day=99))
    assert allowed == []
    assert report.iloc[0]["reason"] in {"same_symbol_daily_reopen_limit_reached", "same_symbol_reopen_same_day_blocked", "reopen_cooldown_active"}


def test_same_symbol_second_open_same_day_is_blocked():
    actions = [{"symbol": "DFTX", "action": "open", "side": "buy"}]
    history = [{"symbol": "DFTX", "opened_at": (NOW - timedelta(hours=1)).isoformat(), "direction": "long", "action": "open"}]
    allowed, report = guard_actions(actions, trade_history=history, now=NOW, config=AntiChurnConfig(max_opens_per_symbol_per_day=1))
    assert allowed == []
    assert report.iloc[0]["reason"] == "same_symbol_daily_open_limit_reached"


def test_dftx_like_repeated_open_close_sequence_is_flagged_as_churn():
    actions = [{"symbol": "DFTX", "action": "open", "side": "buy"}]
    history = [
        {"symbol": "DFTX", "opened_at": (NOW - timedelta(minutes=20)).isoformat(), "closed_at": (NOW - timedelta(minutes=19)).isoformat(), "direction": "long"},
        {"symbol": "DFTX", "opened_at": (NOW - timedelta(minutes=10)).isoformat(), "closed_at": (NOW - timedelta(minutes=9)).isoformat(), "direction": "long"},
    ]
    allowed, report = guard_actions(actions, trade_history=history, now=NOW, config=AntiChurnConfig(max_opens_per_symbol_per_day=1))
    assert allowed == []
    assert report.iloc[0]["reason"] in {"same_symbol_daily_open_limit_reached", "same_symbol_daily_reopen_limit_reached"}
