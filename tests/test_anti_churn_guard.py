from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stockml.trading.anti_churn_guard import ANTI_CHURN_REPORT_COLUMNS, AntiChurnConfig, guard_actions

NOW = datetime(2026, 6, 18, 14, 30, tzinfo=timezone.utc)


def test_buy_then_sell_same_symbol_same_cycle_is_blocked():
    actions = [
        {"symbol": "AAA", "action": "open", "side": "buy"},
        {"symbol": "AAA", "action": "close", "side": "sell", "reason": "monitor_close"},
    ]
    allowed, report = guard_actions(actions, now=NOW, cycle_id="cycle1")
    assert allowed == []
    assert set(report["reason"]) == {"same_cycle_open_close"}
    assert set(report["decision"]) == {"manual_review"}


def test_close_before_minimum_hold_is_blocked():
    actions = [{"symbol": "AAA", "action": "close", "side": "sell", "reason": "monitor_close"}]
    positions = [{"symbol": "AAA", "opened_at": NOW - timedelta(minutes=5)}]
    allowed, report = guard_actions(actions, open_positions=positions, now=NOW)
    assert allowed == []
    assert report.iloc[0]["reason"] == "minimum_hold_not_met"


def test_close_before_minimum_hold_allowed_for_hard_stop():
    actions = [{"symbol": "AAA", "action": "close", "side": "sell", "reason": "hard_stop_hit"}]
    positions = [{"symbol": "AAA", "opened_at": NOW - timedelta(minutes=5)}]
    allowed, report = guard_actions(actions, open_positions=positions, now=NOW)
    assert allowed == actions
    assert report.empty


def test_reopen_within_cooldown_is_blocked():
    actions = [{"symbol": "AAA", "action": "open", "side": "buy"}]
    history = [{"symbol": "AAA", "closed_at": NOW - timedelta(minutes=20), "side": "buy"}]
    allowed, report = guard_actions(actions, trade_history=history, now=NOW)
    assert allowed == []
    assert report.iloc[0]["reason"] == "reopen_cooldown_active"


def test_reverse_same_symbol_same_day_is_blocked_after_cooldown():
    actions = [{"symbol": "AAA", "action": "open", "side": "sell"}]
    history = [{"symbol": "AAA", "closed_at": NOW - timedelta(minutes=90), "side": "buy"}]
    allowed, report = guard_actions(actions, trade_history=history, now=NOW)
    assert allowed == []
    assert report.iloc[0]["reason"] == "reverse_same_symbol_same_day"


def test_anti_churn_report_schema_is_stable():
    _, report = guard_actions(
        [{"symbol": "AAA", "action": "close", "side": "sell", "reason": "monitor_close"}],
        open_positions=[{"symbol": "AAA", "opened_at": NOW - timedelta(minutes=1)}],
        now=NOW,
    )
    assert list(report.columns) == ANTI_CHURN_REPORT_COLUMNS
