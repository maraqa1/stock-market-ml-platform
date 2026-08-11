from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from stockml.trading.position_management_decision import (
    build_position_management_decisions,
    decide_position,
    latest_input_frames,
)


NOW = datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


def pos(**updates):
    row = {
        "symbol": "AAA",
        "side": "long",
        "qty": 100,
        "avg_entry_price": 100,
        "current_price": 101,
        "unrealized_plpc": 0.01,
        "peak_pnl_pct": 0.01,
        "source_trade_action": "Long",
        "model_signal_state": "fresh",
        "signal_alignment": "aligned",
        "holding_quality": "strong",
        "rank_status": "top",
        "quote_status": "clean",
        "spread_bps": 5,
        "liquidity_status": "ok",
        "basket_risk_status": "normal",
        "sector_concentration_status": "normal",
        "anti_churn_status": "clear",
        "cooldown_status": "clear",
        "borrow_status": "clean",
        "short_risk_status": "clean",
        "max_allowed_position_qty": 200,
    }
    row.update(updates)
    return row


def test_latest_input_frames_prefers_ai2_single_brain_candidate_plan(monkeypatch, tmp_path):
    from stockml.trading import position_management_decision as module

    portal = tmp_path / "data" / "portal_outputs"
    portal.mkdir(parents=True)
    pd.DataFrame([{"symbol": "HELD", "qty": 1}]).to_csv(portal / "08_alpaca_paper_positions_20260807_120000.csv", index=False)
    pd.DataFrame([{"symbol": "OLD", "source_trade_action": "Long"}]).to_csv(portal / "08_alpaca_paper_order_plan_20260807_120000.csv", index=False)
    ai2_plan = pd.DataFrame(
        [
            {
                "symbol": "AI2",
                "source_trade_action": "Long",
                "trade_quality_status": "approved",
                "order_ready": True,
                "ai2_single_brain_candidate": True,
            }
        ]
    )
    monkeypatch.setattr(module, "execution_ranked_auto_open_frame", lambda root=None: ai2_plan)

    _, _, _, plan = latest_input_frames(tmp_path)

    assert plan["symbol"].tolist() == ["AI2"]
    assert bool(plan.iloc[0]["ai2_single_brain_candidate"]) is True


def action(row):
    return decide_position(row, now=NOW)


def assert_read_only(decision):
    assert decision["would_submit_order"] is False
    assert decision["execution_allowed"] is False
    assert decision["diagnostics_only"] is True


def test_stale_signal_alone_does_not_close():
    decision = action(pos(model_signal_state="stale", unrealized_plpc=0.01))
    assert decision["recommended_action"] == "hold"
    assert_read_only(decision)


def test_unknown_signal_alone_does_not_close():
    decision = action(pos(model_signal_state="unknown", unrealized_plpc=0.0))
    assert decision["recommended_action"] == "hold"


def test_stale_signal_plus_loss_threshold_manual_review_or_close():
    decision = action(pos(model_signal_state="stale", unrealized_plpc=-0.03, source_trade_action=""))
    assert decision["recommended_action"] in {"manual_review", "close"}


def test_hard_stop_recommends_close():
    decision = action(pos(unrealized_plpc=-0.05))
    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "hard_stop_hit"


def test_explicit_monitor_close_recommends_close():
    decision = action(pos(decision="close", decision_reason="operator_close_candidate"))
    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "monitor_close"
    assert_read_only(decision)


def test_hard_stop_overrides_increase():
    decision = action(pos(unrealized_plpc=-0.05, source_trade_action="Long", rank_status="top"))
    assert decision["recommended_action"] == "close"


def test_confirmed_reversal_recommends_close():
    decision = action(pos(signal_alignment="reversed", confirmed_model_reversal=True))
    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "confirmed_model_reversal"


def test_profitable_weakening_edge_recommends_reduce():
    decision = action(pos(unrealized_plpc=0.03, peak_pnl_pct=0.03, holding_quality="avoid"))
    assert decision["recommended_action"] == "reduce"


def test_same_day_avoid_loser_recommends_close():
    decision = action(
        pos(
            trading_stream="same_day",
            max_holding_days=1,
            holding_quality="avoid",
            holding_gate_pass=False,
            holding_gate_reason="holding_edge_not_confirmed",
            unrealized_plpc=-0.001,
        )
    )

    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "same_day_holding_edge_failed"
    assert decision["holding_gate_pass"] is False
    assert decision["holding_gate_reason"] == "holding_edge_not_confirmed"
    assert_read_only(decision)


def test_same_day_avoid_winner_recommends_reduce():
    decision = action(
        pos(
            trading_stream="same_day",
            max_holding_days=1,
            holding_quality="avoid",
            holding_gate_pass=False,
            holding_gate_reason="holding_edge_not_confirmed",
            unrealized_plpc=0.004,
        )
    )

    assert decision["recommended_action"] == "reduce"
    assert decision["primary_reason"] == "same_day_holding_edge_failed_profitable"
    assert decision["recommended_delta_qty"] < 0


def test_same_day_avoid_with_confirmed_reversal_recommends_close():
    decision = action(
        pos(
            trading_stream="same_day",
            holding_quality="avoid",
            holding_gate_pass=False,
            holding_gate_reason="holding_edge_not_confirmed",
            confirmed_model_reversal=True,
            signal_alignment="reversed",
            unrealized_plpc=0.004,
        )
    )

    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "confirmed_model_reversal"


def test_oversized_position_reduces_to_planned_quantity():
    decision = action(pos(qty=347, planned_suggested_quantity=34, unrealized_plpc=0.001))

    assert decision["recommended_action"] == "reduce"
    assert decision["primary_reason"] == "position_exceeds_approved_plan_size"
    assert decision["recommended_target_qty"] == 34
    assert decision["recommended_delta_qty"] == -313


def test_missing_holding_review_does_not_force_close():
    decision = action(pos(holding_quality="", holding_gate_pass="", holding_gate_reason="", trading_stream="same_day", unrealized_plpc=-0.001))

    assert decision["recommended_action"] != "close"
    assert decision["primary_reason"] != "same_day_holding_edge_failed"


def test_profitable_weakening_position_with_replacement_takes_profit():
    decision = action(
        pos(
            unrealized_plpc=0.03,
            peak_pnl_pct=0.03,
            holding_quality="avoid",
            replacement_symbol="SNOW",
            replacement_edge_bps=225,
            replacement_quality_status="approved",
            replacement_risk_tier="high_quality",
        )
    )
    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "take_profit_hit"
    assert decision["replacement_symbol"] == "SNOW"
    assert "eligible_replacement_available" in decision["supporting_reasons"]


def test_profitable_replacement_requires_eligible_candidate():
    decision = action(
        pos(
            unrealized_plpc=0.03,
            peak_pnl_pct=0.03,
            holding_quality="avoid",
            replacement_symbol="SNOW",
            replacement_edge_bps=225,
            replacement_quality_status="rejected",
        )
    )
    assert decision["recommended_action"] == "reduce"


def test_central_brain_replace_closes_weak_position_with_eligible_replacement():
    decision = action(
        pos(
            decision="replace",
            decision_reason="replacement_rank_improvement",
            unrealized_plpc=-0.025,
            holding_quality="avoid",
            replacement_symbol="SNOW",
            replacement_edge_bps=225,
            replacement_quality_status="approved",
            replacement_risk_tier="high_quality",
        )
    )

    assert decision["recommended_action"] == "replace"
    assert decision["primary_reason"] == "central_brain_replace_weak_position"
    assert decision["recommended_target_qty"] == 0
    assert decision["recommended_delta_qty"] == -100
    assert "central_brain_replacement" in decision["supporting_reasons"]


def test_fresh_position_replacement_waits_for_minimum_hold():
    decision = action(
        pos(
            decision="replace",
            decision_reason="replacement_rank_improvement",
            position_age_minutes=6,
            unrealized_plpc=-0.003,
            holding_quality="watch",
            replacement_symbol="SNOW",
            replacement_edge_bps=225,
            replacement_quality_status="approved",
            replacement_risk_tier="high_quality",
        )
    )

    assert decision["recommended_action"] == "hold"
    assert decision["primary_reason"] == "minimum_hold_period_not_met"
    assert decision["blocking_guard"] == "minimum_hold_period_not_met"


def test_fresh_position_hard_stop_can_close_before_minimum_hold():
    decision = action(pos(position_age_minutes=6, unrealized_plpc=-0.05))
    assert decision["recommended_action"] == "close"
    assert decision["primary_reason"] == "hard_stop_hit"


def test_fresh_profitable_position_edge_deterioration_waits_for_minimum_hold():
    decision = action(pos(position_age_minutes=6, unrealized_plpc=0.003, peak_pnl_pct=0.003, holding_quality="watch"))
    assert decision["recommended_action"] == "hold"
    assert decision["primary_reason"] == "minimum_hold_period_not_met"
    assert decision["blocking_guard"] == "minimum_hold_period_not_met"


def test_profitable_strong_aligned_signal_can_increase_when_below_cap():
    decision = action(pos(unrealized_plpc=0.03, peak_pnl_pct=0.03, qty=100, max_allowed_position_qty=150))
    assert decision["recommended_action"] in {"hold", "increase"}


def test_reduce_has_partial_target_quantity():
    decision = action(pos(unrealized_plpc=0.03, peak_pnl_pct=0.05, holding_quality="watch"))
    assert decision["recommended_action"] == "reduce"
    assert 0 < decision["recommended_target_qty"] < decision["qty"]
    assert decision["recommended_delta_qty"] < 0


def test_close_has_target_quantity_zero():
    decision = action(pos(unrealized_plpc=-0.05))
    assert decision["recommended_action"] == "close"
    assert decision["recommended_target_qty"] == 0


def test_increase_blocked_when_source_trade_action_no_decision():
    decision = action(pos(source_trade_action="No Decision", directional_action="Long", unrealized_plpc=0.03))
    assert decision["recommended_action"] == "hold"
    assert decision["blocking_guard"] == "source_trade_action_no_decision"


def test_increase_blocked_when_only_directional_action_is_available():
    decision = action(pos(source_trade_action="", directional_action="Long", unrealized_plpc=0.03))
    assert decision["recommended_action"] == "hold"
    assert decision["blocking_guard"] == "source_trade_action_missing"


def test_increase_blocked_when_position_at_cap():
    decision = action(pos(qty=100, max_allowed_position_qty=100, unrealized_plpc=0.03))
    assert decision["recommended_action"] == "hold"
    assert decision["blocking_guard"] == "position_cap_reached"


def test_increase_blocked_when_quote_spread_too_wide():
    decision = action(pos(unrealized_plpc=0.03, spread_bps=99))
    assert decision["recommended_action"] == "hold"
    assert decision["blocking_guard"] == "spread_too_wide"


def test_increase_blocked_when_basket_risk_elevated():
    decision = action(pos(unrealized_plpc=0.03, basket_risk_status="elevated"))
    assert decision["recommended_action"] in {"hold", "reduce"}
    assert decision["recommended_action"] != "increase"


def test_short_increase_blocked_by_default():
    decision = action(pos(side="short", qty=-100, source_trade_action="Short", unrealized_plpc=0.03))
    assert decision["recommended_action"] != "increase"
    assert decision["blocking_guard"] in {"short_add_disabled", ""}


def test_short_close_recommended_on_squeeze_risk():
    decision = action(pos(side="short", qty=-100, short_risk_status="squeeze_risk", unrealized_plpc=-0.01))
    assert decision["recommended_action"] == "close"


def test_short_missing_borrow_does_not_force_close_for_small_loss():
    decision = action(pos(side="short", qty=-100, source_trade_action="", borrow_status="", short_risk_status="", unrealized_plpc=-0.006))
    assert decision["recommended_action"] != "close"


def test_open_order_for_symbol_blocks_new_action():
    decision = action(pos(open_order_status="new", pending_action_id="order-1", unrealized_plpc=-0.05))
    assert decision["recommended_action"] == "hold"
    assert decision["blocking_guard"] == "symbol_already_has_open_order"


def test_missing_entry_price_produces_manual_review():
    row = pos(avg_entry_price="")
    decision = action(row)
    assert decision["recommended_action"] == "manual_review"
    assert decision["data_quality_status"] == "insufficient_data"


def test_ambiguous_position_state_produces_manual_review():
    decision = action(pos(side="long", qty=-100))
    assert decision["recommended_action"] == "manual_review"
    assert decision["data_quality_status"] == "ambiguous"


def test_green_position_with_giveback_below_threshold_holds():
    decision = action(pos(unrealized_plpc=0.025, peak_pnl_pct=0.03))
    assert decision["recommended_action"] in {"hold", "increase"}


def test_green_position_with_large_giveback_reduces_or_closes():
    decision = action(pos(unrealized_plpc=0.02, peak_pnl_pct=0.06, holding_quality="watch"))
    assert decision["recommended_action"] in {"reduce", "close"}


def test_reduce_preferred_over_close_when_profitable_without_reversal():
    decision = action(pos(unrealized_plpc=0.03, peak_pnl_pct=0.05, holding_quality="avoid", confirmed_model_reversal=False))
    assert decision["recommended_action"] == "reduce"


def test_output_invariants_always_read_only():
    decision = action(pos(unrealized_plpc=-0.05))
    assert_read_only(decision)


def test_dataframe_builder_outputs_one_action_per_position():
    positions = pd.DataFrame([pos(symbol="AAA"), pos(symbol="BBB", unrealized_plpc=-0.05)])
    decisions = build_position_management_decisions(positions, now=NOW)
    assert len(decisions) == 2
    assert decisions["symbol"].tolist() == ["AAA", "BBB"]
    assert decisions["recommended_action"].notna().all()
    assert decisions["diagnostics_only"].eq(True).all()
