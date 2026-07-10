from datetime import datetime, timedelta, timezone

from stockml.trading.anti_churn_guard import AntiChurnConfig, guard_actions


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)


def test_fresh_position_close_before_minimum_hold_is_blocked():
    actions = [{"symbol": "DFTX", "action": "close", "side": "sell", "reason": "position_management_action"}]
    positions = [{"symbol": "DFTX", "opened_at": (NOW - timedelta(minutes=5)).isoformat()}]
    allowed, report = guard_actions(actions, open_positions=positions, now=NOW, config=AntiChurnConfig(minimum_hold_minutes=30))
    assert allowed == []
    assert report.iloc[0]["reason"] == "minimum_hold_not_met"


def test_fresh_position_close_before_minimum_hold_allowed_for_hard_stop():
    actions = [{"symbol": "DFTX", "action": "close", "side": "sell", "reason": "hard_stop_hit"}]
    positions = [{"symbol": "DFTX", "opened_at": (NOW - timedelta(minutes=5)).isoformat()}]
    allowed, report = guard_actions(actions, open_positions=positions, now=NOW, config=AntiChurnConfig(minimum_hold_minutes=30))
    assert allowed == actions
    assert report.empty
