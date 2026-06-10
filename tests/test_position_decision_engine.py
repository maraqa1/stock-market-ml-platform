from datetime import datetime, timezone

import pandas as pd

from stockml.agents.position_decision_engine import build_position_decisions, find_edge_replacement


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


def test_close_when_hard_stop_loss_threshold_is_triggered():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 5, "current_price": 140, "side": "long", "unrealized_plpc": -0.041}])
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_action": "Long", "signal_generated_at": "2026-05-08T15:55:00Z", "stop_loss_price": 135}])
    decisions = build_position_decisions(positions, plan, now=NOW)
    assert decisions.iloc[0]["decision"] == "close"
    assert "hard_stop_loss_triggered" in decisions.iloc[0]["decision_reason"]


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


def test_edge_replacement_prefers_approved_high_quality_over_reduced_higher_edge():
    shortlist = pd.DataFrame(
        [
            {
                "symbol": "APPS",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 1,
                "trade_quality_status": "reduced",
                "risk_tier": "medium",
                "order_eligible": True,
                "suggested_quantity": 20,
                "expected_trade_return": 0.50,
                "risk_adjusted_score": 0.10,
            },
            {
                "symbol": "SNOW",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 175,
                "trade_quality_status": "approved",
                "risk_tier": "high_quality",
                "order_eligible": True,
                "suggested_quantity": 10,
                "expected_trade_return": 0.22,
                "risk_adjusted_score": 0.05,
            },
        ]
    )

    replacement = find_edge_replacement(
        "AGL",
        shortlist,
        pd.DataFrame([{"symbol": "AGL", "status": "open"}]),
        position_bias="Long",
    )

    assert replacement is not None
    assert replacement["symbol"] == "SNOW"


def test_weak_held_position_gets_review_only_edge_replacement():
    positions = pd.DataFrame([{"symbol": "AGL", "qty": 19, "current_price": 102.16, "avg_entry_price": 98.41, "side": "long", "unrealized_plpc": 0.0381}])
    plan = pd.DataFrame([{"symbol": "AGL", "trade_action": "Long", "signal_generated_at": "2026-05-08T15:55:00Z"}])
    holding_review = pd.DataFrame(
        [
            {
                "symbol": "AGL",
                "trading_stream": "same_day",
                "max_holding_days": 1,
                "holding_quality": "avoid",
                "holding_gate_reason": "holding_edge_not_confirmed",
            }
        ]
    )
    candidate_pool = pd.DataFrame(
        [
            {
                "symbol": "APPS",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 1,
                "trade_quality_status": "reduced",
                "risk_tier": "medium",
                "order_eligible": True,
                "suggested_quantity": 20,
                "expected_trade_return": 0.50,
                "risk_adjusted_score": 0.10,
            },
            {
                "symbol": "SNOW",
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 175,
                "trade_quality_status": "approved",
                "risk_tier": "high_quality",
                "order_eligible": True,
                "suggested_quantity": 10,
                "expected_trade_return": 0.22,
                "risk_adjusted_score": 0.05,
            },
        ]
    )

    decisions = build_position_decisions(positions, plan, candidate_pool=candidate_pool, holding_review=holding_review, now=NOW)
    row = decisions.iloc[0]

    assert row["decision"] == "replace"
    assert row["recommended_action"] == "review_edge_replacement"
    assert row["replacement_symbol"] == "SNOW"
    assert row["replacement_selection_method"] == "edge"
    assert row["replacement_quality_status"] == "approved"
    assert row["replacement_risk_tier"] == "high_quality"
    assert "replacement_edge_improvement" in row["decision_reason"]
