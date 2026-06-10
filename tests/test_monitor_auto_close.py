from __future__ import annotations

import pandas as pd

from stockml.trading.monitor_auto_close import execute_monitor_auto_closes, monitor_close_candidates


def test_monitor_close_candidates_include_stop_loss_replacements():
    decisions = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "close", "recommended_action": "close_position", "decision_reason": "stop_loss_triggered"},
            {"symbol": "BBB", "decision": "replace", "recommended_action": "close_then_open_replacement", "decision_reason": "stop_loss_triggered|replacement_available"},
            {"symbol": "EEE", "decision": "replace", "recommended_action": "close_then_open_replacement", "decision_reason": "hard_stop_loss_triggered|replacement_available"},
            {"symbol": "DDD", "decision": "replace", "recommended_action": "close_then_open_replacement", "decision_reason": "replacement_rank_improvement"},
            {"symbol": "CCC", "decision": "watch", "recommended_action": "manual_review", "decision_reason": "signal_stale"},
        ]
    )

    candidates = monitor_close_candidates(decisions)

    assert candidates["symbol"].tolist() == ["AAA", "BBB", "EEE"]


def test_execute_monitor_auto_closes_skips_when_not_automatic():
    decisions = pd.DataFrame([{"symbol": "AAA", "decision": "close", "recommended_action": "close_position"}])
    calls = []

    result = execute_monitor_auto_closes(
        decisions,
        close_automation_mode="review_only",
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted"},
    )

    assert result["auto_close_status"] == "skipped"
    assert result["auto_close_candidates"] == 1
    assert result["auto_close_attempted"] == 0
    assert calls == []


def test_execute_monitor_auto_closes_submits_explicit_and_stop_loss_replacement_closes():
    decisions = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "close", "recommended_action": "close_position"},
            {"symbol": "BBB", "decision": "replace", "recommended_action": "close_then_open_replacement", "decision_reason": "stop_loss_triggered|replacement_available"},
            {"symbol": "CCC", "decision": "replace", "recommended_action": "close_then_open_replacement", "decision_reason": "replacement_rank_improvement"},
            {"symbol": "AAA", "decision": "close", "recommended_action": "close_position"},
        ]
    )
    calls = []

    result = execute_monitor_auto_closes(
        decisions,
        close_automation_mode="automatic",
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "manual_close_submitted"},
    )

    assert result["auto_close_status"] == "ok"
    assert result["auto_close_candidates"] == 2
    assert result["auto_close_attempted"] == 2
    assert result["auto_close_submitted"] == 2
    assert calls == [("BBB", "close"), ("AAA", "close")]


def test_execute_monitor_auto_closes_counts_dry_run_and_errors():
    decisions = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "close", "recommended_action": "close_position"},
            {"symbol": "BBB", "decision": "close", "recommended_action": "close_position"},
        ]
    )

    def action(symbol: str, action_name: str):
        return {"status": "dry_run" if symbol == "AAA" else "error", "message": symbol}

    result = execute_monitor_auto_closes(decisions, close_automation_mode="automatic", action_func=action)

    assert result["auto_close_attempted"] == 2
    assert result["auto_close_dry_run"] == 1
    assert result["auto_close_error"] == 1


def test_execute_monitor_auto_closes_skips_prior_submitted_close():
    decisions = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "close", "recommended_action": "close_position"},
            {"symbol": "BBB", "decision": "close", "recommended_action": "close_position"},
        ]
    )
    previous = pd.DataFrame([{"symbol": "AAA", "operator_action": "close", "status": "submitted"}])
    calls = []

    result = execute_monitor_auto_closes(
        decisions,
        close_automation_mode="automatic",
        previous_actions=previous,
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted"},
    )

    assert result["auto_close_candidates"] == 2
    assert result["auto_close_skipped_existing"] == 1
    assert result["auto_close_attempted"] == 1
    assert calls == [("BBB", "close")]


def test_execute_monitor_auto_closes_only_skips_active_broker_order_symbols():
    decisions = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "close", "recommended_action": "close_position"},
            {"symbol": "BBB", "decision": "close", "recommended_action": "close_position"},
        ]
    )
    previous = pd.DataFrame([{"symbol": "AAA", "operator_action": "close", "status": "submitted"}])
    calls = []

    result = execute_monitor_auto_closes(
        decisions,
        close_automation_mode="automatic",
        previous_actions=previous,
        active_order_symbols={"BBB"},
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted"},
    )

    assert result["auto_close_candidates"] == 2
    assert result["auto_close_skipped_existing"] == 1
    assert result["auto_close_attempted"] == 1
    assert calls == [("AAA", "close")]
