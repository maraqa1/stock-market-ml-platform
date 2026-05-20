from pathlib import Path
import time

import pandas as pd

from portal.services.latest_file_reader import latest_file, readable_reason
from portal.services.trading_api_service import action_queue_context, positions_context
from portal.services.universe_service import universe_context
from portal.services.signal_service import signal_context
from portal.services.trading_service import lifecycle_context, trading_context


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_latest_file_selection(tmp_path):
    older = tmp_path / "data" / "raw" / "01_us_equity_universe_old.csv"
    newer = tmp_path / "data" / "raw" / "01_us_equity_universe_new.csv"
    write_csv(older, [{"ticker": "AAA"}])
    time.sleep(0.01)
    write_csv(newer, [{"ticker": "BBB"}])
    assert latest_file(tmp_path, "raw", "01_us_equity_universe_*.csv") == newer


def test_missing_file_behavior(tmp_path):
    ctx = universe_context(tmp_path)
    assert ctx["raw_count"] == 0
    assert ctx["tradable_count"] == 0
    assert ctx["files"][0]["exists"] is False


def test_reason_formatter():
    assert readable_reason("weak_probability") == "Probability below decision threshold"
    assert readable_reason("not_in_top_ranked_long_or_short_candidates") == "Not ranked strongly enough today"


def test_signal_context_with_fixture(tmp_path):
    write_csv(
        tmp_path / "data" / "model_outputs" / "advanced_model_signal_table_1.csv",
        [
            {"ticker": "AAA", "trade_action": "Long", "signal_reason": "strong_probability"},
            {"ticker": "BBB", "trade_action": "No Decision", "no_decision_reason": "weak_probability"},
        ],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "advanced_model_model_status_1.csv",
        [{"decision_grade": "decision_grade", "selected_model": "LightGBM", "reason": "ok"}],
    )
    ctx = signal_context(tmp_path)
    assert ctx["long_count"] == 1
    assert ctx["no_decision_count"] == 1


def test_signal_context_sorts_highest_confidence_first(tmp_path):
    write_csv(
        tmp_path / "data" / "model_outputs" / "advanced_model_signal_table_1.csv",
        [
            {"ticker": "LOW", "trade_action": "Long", "side_probability": 0.61, "probability_edge": 0.11, "risk_adjusted_score": 0.9},
            {"ticker": "HIGH", "trade_action": "Long", "side_probability": 0.82, "probability_edge": 0.12, "risk_adjusted_score": 0.1},
            {"ticker": "MID", "trade_action": "Long", "side_probability": 0.74, "probability_edge": 0.25, "risk_adjusted_score": 0.2},
        ],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "advanced_model_model_status_1.csv",
        [{"decision_grade": "decision_grade", "selected_model": "LightGBM", "reason": "ok"}],
    )
    ctx = signal_context(tmp_path)
    assert [row["ticker"] for row in ctx["long_rows"]] == ["HIGH", "MID", "LOW"]


def test_trading_context_with_alpaca_artifacts(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
        [
            {"candidate_rank": 2, "symbol": "BBB", "trade_action": "Short", "trade_quality_status": "rejected", "side": "sell"},
            {"candidate_rank": 1, "symbol": "AAA", "trade_action": "Long", "trade_quality_status": "reduced", "side": "buy"},
        ],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_1.csv",
        [{"symbol": "AAA", "side": "buy", "notional": 500, "trade_action": "Long", "side_probability": 0.7, "suggested_quantity": 2, "trade_quality_status": "reduced", "client_order_id": "stockml-AAA"}],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_results_1.csv",
        [{"symbol": "AAA", "status": "dry_run", "order_id": "", "client_order_id": "stockml-AAA", "message": "disabled"}],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_tracking_1.csv",
        [{"symbol": "AAA", "status": "dry_run", "alpaca_status": "", "order_id": "", "client_order_id": "stockml-AAA"}],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "AAA", "qty": 2, "market_value": 220, "cost_basis": 200, "unrealized_pl": 20}],
    )
    ctx = trading_context(tmp_path)
    assert ctx["orders_planned"] == 1
    assert ctx["orders_submitted"] == 0
    assert ctx["dry_run"] is True
    assert ctx["total_notional"] == 500
    assert ctx["orders_tracked"] == 1
    assert ctx["tracking_rows"][0]["client_order_id"] == "stockml-AAA"
    assert ctx["position_market_value"] == 220
    assert ctx["position_cost_basis"] == 200
    assert ctx["position_unrealized_pl"] == 20
    assert ctx["position_unrealized_plpc"] == 0.1
    assert ctx["candidate_pool_count"] == 2
    assert ctx["candidate_pool_action_counts"] == {"Short": 1, "Long": 1}
    assert ctx["candidate_pool_status_counts"] == {"rejected": 1, "reduced": 1}
    assert [row["symbol"] for row in ctx["candidate_pool_rows"]] == ["AAA", "BBB"]
    assert ctx["basket_rows"][0]["symbol"] == "AAA"
    assert ctx["basket_rows"][0]["planned_quantity"] == 2
    assert ctx["basket_rows"][0]["basket_status"] == "trimmed"
    assert ctx["basket_rows"][0]["reason_note"] == "Disabled"
    assert ctx["rejected_trimmed_count"] == 1
    assert ctx["rejected_trimmed_rows"][0]["symbol"] == "AAA"
    assert ctx["rejected_trimmed_rows"][0]["status"] == "trimmed"
    assert ctx["rejected_trimmed_rows"][0]["source"] == "Guardrail"
    assert {row["label"]: row["value"] for row in ctx["execution_quality"]}["Rejected / Error"] == 0
    assert {row["label"]: row["value"] for row in ctx["execution_quality"]}["Fill ratio"] == "Not available"


def test_trading_context_rejected_trimmed_sources(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_1.csv",
        [
            {"symbol": "AAA", "side": "buy", "trade_action": "Long", "suggested_quantity": 2, "trade_quality_status": "reduced", "trade_quality_reason": "reduced", "client_order_id": "stockml-AAA"},
            {"symbol": "BBB", "side": "sell", "trade_action": "Short", "suggested_quantity": 0, "trade_quality_status": "rejected", "trade_quality_reason": "market_cap_below_minimum", "client_order_id": "stockml-BBB"},
            {"symbol": "CCC", "side": "buy", "trade_action": "Long", "suggested_quantity": 1, "trade_quality_status": "approved", "client_order_id": "stockml-CCC"},
        ],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_results_1.csv",
        [
            {"symbol": "AAA", "status": "dry_run", "client_order_id": "stockml-AAA", "message": ""},
            {"symbol": "BBB", "status": "rejected", "client_order_id": "stockml-BBB", "message": "market_cap_below_minimum"},
            {"symbol": "CCC", "status": "error", "client_order_id": "stockml-CCC", "message": "alpaca_order_submit_failed", "http_status": 422, "api_error": "bad request"},
        ],
    )
    ctx = trading_context(tmp_path)
    rows = {row["symbol"]: row for row in ctx["rejected_trimmed_rows"]}
    assert ctx["rejected_trimmed_count"] == 3
    assert rows["AAA"]["status"] == "trimmed"
    assert rows["AAA"]["source"] == "Guardrail"
    assert rows["BBB"]["status"] == "rejected"
    assert rows["BBB"]["source"] == "Guardrail"
    assert rows["CCC"]["status"] == "failed"
    assert rows["CCC"]["source"] == "Broker"


def test_position_summary_uses_gross_exposure_for_short_positions(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [
            {"symbol": "LONG", "qty": 2, "market_value": 110, "cost_basis": 100, "unrealized_pl": 10},
            {"symbol": "SHORT", "qty": -2, "market_value": -90, "cost_basis": -100, "unrealized_pl": 10},
        ],
    )

    ctx = trading_context(tmp_path)

    assert ctx["position_market_value"] == 200
    assert ctx["position_cost_basis"] == 200
    assert ctx["position_unrealized_pl"] == 20
    assert ctx["position_unrealized_plpc"] == 0.1


def test_trading_context_sorts_plan_by_confidence(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_1.csv",
        [
            {"symbol": "LOW", "side": "buy", "notional": 500, "trade_action": "Long", "side_probability": 0.6},
            {"symbol": "HIGH", "side": "buy", "notional": 500, "trade_action": "Long", "side_probability": 0.8},
        ],
    )
    ctx = trading_context(tmp_path)
    assert [row["symbol"] for row in ctx["plan_rows"]] == ["HIGH", "LOW"]


def test_lifecycle_context_with_artifacts(tmp_path):
    write_csv(
        tmp_path / "data" / "trading" / "paper_trade_journal" / "paper_trade_journal_1.csv",
        [{"symbol": "FLEX", "lifecycle_state": "order_planned", "trade_quality_status": "approved", "approved_notional": 1000}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "paper_pnl" / "paper_pnl_1.csv",
        [{"symbol": "FLEX", "qty": 2, "market_value": 220, "cost_basis": 200, "unrealized_pl": 20}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [{"symbol": "FLEX", "decision": "hold", "recommended_action": "keep_position", "decision_reason": "position_within_rules"}],
    )
    ctx = lifecycle_context(tmp_path)
    assert ctx["journal_rows_count"] == 1
    assert ctx["order_planned_count"] == 1
    assert ctx["position_count"] == 1
    assert ctx["unrealized_pl"] == 20
    assert ctx["decision_counts"]["hold"] == 1


def test_action_queue_adds_operator_calls_for_visible_supervision(tmp_path):
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [
            {
                "symbol": "FRMI",
                "decision": "replace",
                "recommended_action": "close_then_open_replacement",
                "decision_reason": "signal_stale|replacement_rank_improvement",
                "replacement_symbol": "FWRD",
                "unrealized_pl": 22.34,
                "unrealized_plpc": 0.0459,
            },
            {
                "symbol": "FWRD",
                "decision": "watch",
                "recommended_action": "rescore_before_add_or_hold",
                "decision_reason": "signal_stale",
                "unrealized_pl": -13.61,
                "unrealized_plpc": -0.0271,
            },
            {
                "symbol": "GLIBK",
                "decision": "replace",
                "recommended_action": "close_then_open_replacement",
                "decision_reason": "take_profit_triggered|replacement_available",
                "replacement_symbol": "FWRD",
                "unrealized_pl": -9.53,
                "unrealized_plpc": -0.0186,
            },
        ],
    )

    ctx = action_queue_context(tmp_path)
    rows = {row["symbol"]: row for row in ctx["items"]}

    assert rows["FWRD"]["decision"] == "close_candidate"
    assert rows["FWRD"]["operator_call_label"] == "Auto close"
    assert rows["FWRD"]["operator_apply_enabled"] is False
    assert rows["FRMI"]["operator_call_label"] == "Review concentration"
    assert rows["FRMI"]["operator_apply_enabled"] is False
    assert rows["GLIBK"]["operator_call_label"] == "Hold - logic check"
    assert rows["GLIBK"]["operator_apply_enabled"] is False


def test_positions_context_adds_position_management_intelligence(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "CAI", "qty": 10, "market_value": 150.35, "cost_basis": 150, "unrealized_pl": 0.35, "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [{"symbol": "CAI", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": 0.00231}],
    )
    state_path = tmp_path / "data" / "portal_outputs" / "paper_autopilot_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"position_peak_plpc":{"CAI":0.03498}}', encoding="utf-8")

    ctx = positions_context(tmp_path)
    row = ctx["positions"][0]

    assert row["position_intelligence_management_state"] == "close_triggered"
    assert row["position_intelligence"]["close_trigger_reason"] == "trailing_profit_giveback"
    assert row["position_intelligence"]["signal_state"] == "unknown"


def test_positions_context_uses_latest_model_signal_for_management_health(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "MGT", "qty": 10, "market_value": 150.35, "cost_basis": 150, "unrealized_pl": 0.35, "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [{"symbol": "MGT", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "model_predictions_latest.csv",
        [{"ticker": "MGT", "trade_action": "Long", "risk_adjusted_score": 1.23}],
    )

    ctx = positions_context(tmp_path)
    row = ctx["positions"][0]

    assert row["latest_signal_status"] == "fresh"
    assert row["latest_signal_direction"] == "long"
    assert row["model_status"] == "decision_grade"
    assert row["position_health_status"] == "healthy_hold"
    assert row["position_health_reason"] == "green_position_no_risk_issue"


def test_action_queue_uses_latest_model_signal_for_held_positions(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "MGT", "qty": 10, "market_value": 150.35}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [{"symbol": "MGT", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "model_predictions_latest.csv",
        [{"ticker": "MGT", "trade_action": "Long", "risk_adjusted_score": 1.23}],
    )

    ctx = action_queue_context(tmp_path)
    row = ctx["items"][0]

    assert row["latest_signal_status"] == "fresh"
    assert row["latest_signal_direction"] == "long"
    assert row["model_status"] == "decision_grade"
    assert row["decision_reason"] == "latest_signal_fresh"
    assert row["position_health_reason"] == "green_position_no_risk_issue"


def test_action_queue_labels_no_decision_as_fresh_hold(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "MGT", "qty": 10, "market_value": 150.35}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [{"symbol": "MGT", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "model_predictions_latest.csv",
        [{"ticker": "MGT", "trade_action": "No Decision", "signal": "HOLD", "risk_adjusted_score": 1.23}],
    )

    ctx = action_queue_context(tmp_path)
    row = ctx["items"][0]

    assert row["latest_signal_status"] == "fresh"
    assert row["latest_signal"] == "HOLD"
    assert row["latest_signal_direction"] == "hold"
    assert row["decision_reason"] == "latest_signal_fresh"


def test_action_queue_labels_missing_latest_model_signal(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "MGT", "qty": 10, "market_value": 150.35}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [{"symbol": "MGT", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "model_predictions_latest.csv",
        [{"ticker": "OTHER", "trade_action": "Long", "signal": "LONG", "risk_adjusted_score": 1.23}],
    )

    ctx = action_queue_context(tmp_path)
    row = ctx["items"][0]

    assert row["latest_signal_status"] == "missing"
    assert row["latest_signal_direction"] == "missing"
    assert row["model_status"] == "not_in_latest_model_output"
    assert row["decision_reason"] == "latest_model_signal_missing"
    assert row["position_health_reason"] == "latest_model_signal_missing_green_position"


def test_action_queue_includes_candidate_evaluation_opportunities(tmp_path):
    write_csv(
        tmp_path / "data" / "trading" / "candidate_evaluations" / "candidate_evaluation_1.csv",
        [
            {
                "symbol": "AAA",
                "side": "long",
                "candidate_rank": 1,
                "current_price": 10,
                "decision": "open_candidate",
                "recommended_action": "review_open_candidate",
                "decision_reason": "candidate_slot_available",
                "operator_call_text": "Review candidate for possible paper entry.",
            },
            {
                "symbol": "BBB",
                "side": "long",
                "candidate_rank": 2,
                "current_price": 11,
                "decision": "skip",
                "recommended_action": "skip_candidate",
                "decision_reason": "risk_or_quality_rejected",
            },
        ],
    )

    ctx = action_queue_context(tmp_path)
    rows = {row["symbol"]: row for row in ctx["items"]}

    assert "AAA" in rows
    assert "BBB" not in rows
    assert rows["AAA"]["operator_call_label"] == "Review open"
    assert rows["AAA"]["operator_apply_enabled"] is False


def test_action_queue_filters_monitor_rows_for_symbols_no_longer_open(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [
            {"symbol": "FRMI", "qty": 94, "market_value": 505.25},
            {"symbol": "CERT", "qty": 24, "market_value": 120.36},
        ],
    )
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [
            {
                "symbol": "FWRD",
                "decision": "close",
                "recommended_action": "close_position",
                "decision_reason": "stop_loss_triggered",
                "unrealized_pl": -2.88,
                "unrealized_plpc": -0.0129,
            },
            {
                "symbol": "CERT",
                "decision": "watch",
                "recommended_action": "rescore_before_add_or_hold",
                "decision_reason": "signal_stale",
                "unrealized_pl": -1.56,
                "unrealized_plpc": -0.0128,
            },
        ],
    )

    ctx = action_queue_context(tmp_path)
    rows = {row["symbol"]: row for row in ctx["items"]}

    assert "FWRD" not in rows
    assert rows["CERT"]["operator_call_label"] == "Watch only"


def test_action_queue_does_not_show_held_symbol_as_open_candidate(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "CSTL", "qty": 9, "market_value": 169.60}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "candidate_evaluations" / "candidate_evaluation_1.csv",
        [
            {
                "symbol": "CSTL",
                "side": "long",
                "candidate_rank": 1,
                "decision": "open_candidate",
                "recommended_action": "review_open_candidate",
                "decision_reason": "candidate_slot_available",
            },
            {
                "symbol": "ADMA",
                "side": "long",
                "candidate_rank": 2,
                "decision": "open_candidate",
                "recommended_action": "review_open_candidate",
                "decision_reason": "candidate_slot_available",
            },
        ],
    )

    ctx = action_queue_context(tmp_path)
    rows = {row["symbol"]: row for row in ctx["items"]}

    assert "CSTL" not in rows
    assert "ADMA" in rows


def test_action_queue_filters_rotation_rows_for_symbols_no_longer_open(monkeypatch, tmp_path):
    write_csv(tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv", [])

    monkeypatch.setattr(
        "portal.services.trading_api_service._rows_from_db",
        lambda *args, **kwargs: [
            {
                "id": 7,
                "replace_symbol": "CSTL",
                "with_symbol": "ATEC",
                "score_delta": 0.15,
                "reason": "HIGHER_PROMOTION_SCORE",
                "verdict": "proposed",
                "replace_position_id": "paper:CSTL",
                "logged_at": "2026-05-12T15:29:40+00:00",
            }
        ],
    )

    ctx = action_queue_context(tmp_path)

    assert ctx["items"] == []


def test_action_queue_filters_rotation_rows_when_replacement_already_held(monkeypatch, tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [
            {"symbol": "CSTL", "qty": 10, "market_value": 150},
            {"symbol": "ATEC", "qty": 5, "market_value": 150},
        ],
    )

    monkeypatch.setattr(
        "portal.services.trading_api_service._rows_from_db",
        lambda *args, **kwargs: [
            {
                "id": 7,
                "replace_symbol": "CSTL",
                "with_symbol": "ATEC",
                "score_delta": 0.15,
                "reason": "HIGHER_PROMOTION_SCORE",
                "verdict": "proposed",
                "replace_position_id": "paper:CSTL",
                "logged_at": "2026-05-12T15:29:40+00:00",
            }
        ],
    )

    ctx = action_queue_context(tmp_path)

    assert ctx["items"] == []


def test_action_queue_filters_rotation_rows_when_order_in_flight(monkeypatch, tmp_path):
    write_csv(tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv", [{"symbol": "CSTL", "qty": 10}])
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_tracking_1.csv",
        [{"symbol": "ATEC", "alpaca_status": "pending_new"}],
    )

    monkeypatch.setattr(
        "portal.services.trading_api_service._rows_from_db",
        lambda *args, **kwargs: [
            {
                "id": 7,
                "replace_symbol": "CSTL",
                "with_symbol": "ATEC",
                "score_delta": 0.15,
                "reason": "HIGHER_PROMOTION_SCORE",
                "verdict": "proposed",
                "replace_position_id": "paper:CSTL",
                "logged_at": "2026-05-12T15:29:40+00:00",
            }
        ],
    )

    ctx = action_queue_context(tmp_path)

    assert ctx["items"] == []
