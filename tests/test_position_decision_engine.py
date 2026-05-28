from datetime import datetime, timezone

import pandas as pd

from stockml.agents.position_decision_engine import build_position_decisions


NOW = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)


def test_hold_when_position_is_inside_rules_and_signal_is_fresh():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 142, "avg_entry_price": 141, "side": "long"}])
    plan = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "trade_action": "Long",
                "signal_generated_at": "2026-05-08T15:55:00Z",
                "stop_loss_price": 135,
                "take_profit_price": 150,
                "max_holding_days": 5,
            }
        ]
    )
    decisions = build_position_decisions(positions, plan, now=NOW)
    assert decisions.iloc[0]["decision"] == "hold"
    assert decisions.iloc[0]["recommended_action"] == "keep_position"


def test_watch_when_signal_is_stale():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 142, "side": "long"}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "Long", "signal_generated_at": "2026-05-08T15:00:00Z"}])
    decisions = build_position_decisions(positions, plan, now=NOW, signal_ttl_minutes=10)
    assert decisions.iloc[0]["decision"] == "watch"
    assert "signal_stale" in decisions.iloc[0]["decision_reason"]


def test_close_when_stop_loss_is_triggered():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 134, "side": "long"}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "Long", "signal_generated_at": "2026-05-08T15:55:00Z", "stop_loss_price": 135}])
    decisions = build_position_decisions(positions, plan, now=NOW)
    assert decisions.iloc[0]["decision"] == "close"
    assert "stop_loss_triggered" in decisions.iloc[0]["decision_reason"]


def test_close_when_take_profit_is_triggered():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 151, "side": "long"}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "Long", "signal_generated_at": "2026-05-08T15:55:00Z", "take_profit_price": 150}])
    decisions = build_position_decisions(positions, plan, now=NOW)
    assert decisions.iloc[0]["decision"] == "close"
    assert "take_profit_triggered" in decisions.iloc[0]["decision_reason"]


def test_holding_review_stream_overrides_plan_max_hold_for_same_day_positions():
    positions = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "qty": 5,
                "current_price": 142,
                "side": "long",
                "submitted_at": "2026-05-07T15:00:00Z",
            }
        ]
    )
    plan = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "trade_action": "Long",
                "signal_generated_at": "2026-05-08T15:55:00Z",
                "max_holding_days": 5,
            }
        ]
    )
    holding_review = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "trading_stream": "same_day",
                "max_holding_days": 1,
                "recommended_holding_days": 1,
            }
        ]
    )

    decisions = build_position_decisions(positions, plan, holding_review=holding_review, now=NOW)
    row = decisions.iloc[0]

    assert row["trading_stream"] == "same_day"
    assert row["max_holding_days"] == 1
    assert row["decision"] == "close"
    assert "max_holding_days_exceeded" in row["decision_reason"]


def test_close_when_signal_no_longer_active():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 142, "side": "long"}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "No Decision", "signal_generated_at": "2026-05-08T15:55:00Z"}])
    decisions = build_position_decisions(positions, plan, now=NOW)
    assert decisions.iloc[0]["decision"] == "close"
    assert "signal_no_longer_active" in decisions.iloc[0]["decision_reason"]


def test_unknown_signal_context_requires_watch_not_close():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 142, "side": "long"}])
    decisions = build_position_decisions(positions, pd.DataFrame(), now=NOW, fallback_signal_time=NOW)
    assert decisions.iloc[0]["decision"] == "watch"
    assert decisions.iloc[0]["recommended_action"] == "manual_review"
    assert "latest_signal_unknown" in decisions.iloc[0]["decision_reason"]


def test_replace_when_close_signal_has_available_candidate():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 142, "side": "long"}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "No Decision", "signal_generated_at": "2026-05-08T15:55:00Z"}])
    candidate_pool = pd.DataFrame(
        [
            {
                "symbol": "ADMA",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 1,
                "trade_quality_status": "reduced",
                "order_eligible": True,
                "suggested_quantity": 10,
            }
        ]
    )
    decisions = build_position_decisions(positions, plan, candidate_pool=candidate_pool, now=NOW)
    row = decisions.iloc[0]
    assert row["decision"] == "replace"
    assert row["replacement_symbol"] == "ADMA"
    assert "replacement_available" in row["decision_reason"]


def test_replace_when_materially_better_same_side_candidate_exists():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 142, "side": "long"}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "Long", "signal_generated_at": "2026-05-08T15:55:00Z"}])
    candidate_pool = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 25,
                "trade_quality_status": "reduced",
                "order_eligible": True,
                "suggested_quantity": 5,
            },
            {
                "symbol": "ADMA",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 5,
                "trade_quality_status": "reduced",
                "order_eligible": True,
                "suggested_quantity": 10,
            },
        ]
    )
    decisions = build_position_decisions(positions, plan, candidate_pool=candidate_pool, now=NOW)
    row = decisions.iloc[0]
    assert row["decision"] == "replace"
    assert row["replacement_symbol"] == "ADMA"
    assert "replacement_rank_improvement" in row["decision_reason"]
