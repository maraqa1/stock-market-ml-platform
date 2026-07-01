from pathlib import Path
import time

import pandas as pd

from portal.services.latest_file_reader import latest_file, readable_reason
from portal.services.kpi import trading_kpi_context
from portal.services.rotation_diagnostic_service import held_vs_candidate_context
from portal.services.trading_api_service import action_queue_context, positions_context
from portal.services.universe_service import universe_context
from portal.services.signal_service import signal_context
from portal.services.trading_service import lifecycle_context, trading_context
from portal.services.trade_ledger_view import trade_ledger_context, trade_ledger_csv


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


def test_trading_context_exposes_position_management_review(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "AAA", "side": "long", "qty": 10, "avg_entry_price": 10, "current_price": 11, "unrealized_pl": 10, "unrealized_plpc": 0.1}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "diagnostics" / "position_management_decisions_1.csv",
        [
            {
                "symbol": "AAA",
                "side": "long",
                "qty": 10,
                "pnl_amount": 10,
                "pnl_pct": 0.1,
                "recommended_action": "reduce",
                "action_strength": "medium",
                "decision_confidence": "high",
                "recommended_target_qty": 5,
                "recommended_delta_qty": -5,
                "primary_reason": "profit_giveback_with_weakening_edge",
                "blocking_guard": "",
                "data_quality_status": "ok",
            }
        ],
    )
    write_csv(
        tmp_path / "data" / "trading" / "autopilot" / "autopilot_ticks_20260701.csv",
        [{"autopilot_action_notes": "AAA:reduce:profit_giveback_with_weakening_edge:submitted:auto_reduce"}],
    )

    ctx = trading_context(tmp_path)
    review = ctx["position_management_review"]

    assert review["row_count"] == 1
    assert review["action_counts"] == {"reduce": 1}
    assert review["review_counts"] == {"submitted": 1}
    assert review["rows"][0]["symbol"] == "AAA"
    assert review["rows"][0]["recommended_action"] == "reduce"
    assert review["rows"][0]["auto_action"] == "reduce"
    assert review["rows"][0]["auto_status"] == "submitted"
    assert review["rows"][0]["review_status"] == "submitted"


def test_position_management_review_explains_loss_making_hold(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "AVAV", "side": "short", "qty": -3, "avg_entry_price": 164.08, "current_price": 165.5, "unrealized_pl": -4.25, "unrealized_plpc": -0.00864}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "diagnostics" / "position_management_decisions_1.csv",
        [
            {
                "symbol": "AVAV",
                "side": "short",
                "qty": 3,
                "pnl_amount": -4.25,
                "pnl_pct": -0.00864,
                "recommended_action": "hold",
                "primary_reason": "no_action_required",
                "blocking_guard": "",
                "data_quality_status": "ok",
            }
        ],
    )

    row = trading_context(tmp_path)["position_management_review"]["rows"][0]

    assert row["review_status"] == "monitoring"
    assert row["manager_explanation"] == "loss below manager close trigger"



def test_trade_ledger_context_loads_latest_diagnostics(tmp_path):
    write_csv(
        tmp_path / "data" / "trading" / "diagnostics" / "trade_ledger_20260630_090000.csv",
        [{"trade_id": "old", "symbol": "OLD", "trade_status": "closed", "realized_pnl_usd": -1}],
    )
    time.sleep(0.01)
    write_csv(
        tmp_path / "data" / "trading" / "diagnostics" / "trade_ledger_20260630_100000.csv",
        [{"trade_id": "new", "symbol": "NEW", "trade_status": "open", "realized_pnl_usd": 0}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "diagnostics" / "profitability_attribution_20260630_100000.csv",
        [{"trade_id": "new", "symbol": "NEW", "total_pnl_usd": 4.5}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "diagnostics" / "unmatched_lifecycle_events_20260630_100000.csv",
        [{"event_id": 1, "symbol": "MISS", "lineage_warning": "missing_candidate_id"}],
    )

    ctx = trade_ledger_context(tmp_path)

    assert ctx["summary"]["trade_count"] == 1
    assert ctx["summary"]["open_trades"] == 1
    assert ctx["summary"]["total_pnl_usd"] == 4.5
    assert ctx["ledger_rows"][0]["trade_id"] == "new"
    assert ctx["unmatched_rows"][0]["lineage_warning"] == "missing_candidate_id"
    assert "new" in trade_ledger_csv(tmp_path, "ledger")


def test_held_vs_candidate_context_compares_positions_and_available_candidates(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "AAA", "side": "long", "qty": 1, "market_value": 9.5, "cost_basis": 10, "unrealized_pl": -0.5, "unrealized_plpc": -0.05}],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
        [
            {"symbol": "AAA", "side": "buy", "trade_quality_status": "rejected", "expected_trade_return": 0.0, "risk_adjusted_score": 0.0},
            {"symbol": "VPG", "side": "buy", "trade_quality_status": "approved", "expected_trade_return": 0.04, "risk_adjusted_score": 0.05},
        ],
    )
    write_csv(
        tmp_path / "data" / "trading" / "holding_period" / "holding_review_1.csv",
        [{"symbol": "AAA", "holding_quality": "avoid", "recommended_action": "avoid"}],
    )

    ctx = held_vs_candidate_context(tmp_path)

    assert ctx["summary"]["open_positions"] == 1
    assert ctx["held_positions"][0]["symbol"] == "AAA"
    assert ctx["held_positions"][0]["rotation_flag"] == "review_close"
    assert ctx["available_candidates"][0]["symbol"] == "VPG"


def test_trading_kpi_labels_gross_and_net_exposure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "portal.services.kpi.alpaca_config",
        lambda: type(
            "Config",
            (),
            {
                "account_equity": 1000,
                "max_orders": 10,
                "paper_trading_enabled": True,
                "live_trading_enabled": False,
                "submit_orders": False,
            },
        )(),
    )
    monkeypatch.setattr(
        "portal.services.kpi.account_snapshot",
        lambda config: {"equity": 1000, "source": "fixture"},
    )

    ctx = trading_kpi_context(
        tmp_path,
        positions={
            "summary": {
                "position_count": 2,
                "position_market_value": 285,
                "position_net_market_value": 0.39,
                "position_unrealized_pl": 1.72,
                "position_unrealized_plpc": 0.0061,
            }
        },
        basket={"counts": {"submitted": 0, "filled": 0}},
        queue={"counts": {"total": 3}},
    )

    cards = {card["label"]: card for card in ctx["cards"]}
    assert cards["Gross Exposure"]["value"] == "$285"
    assert cards["Gross Exposure"]["detail"] == "Net $0 - 28.50% gross of equity"
    assert "Net Exposure" not in cards


def test_trading_kpi_counts_only_action_required_queue_items(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "portal.services.kpi.alpaca_config",
        lambda: type(
            "Config",
            (),
            {
                "account_equity": 1000,
                "max_orders": 10,
                "paper_trading_enabled": True,
                "live_trading_enabled": False,
                "submit_orders": False,
            },
        )(),
    )
    monkeypatch.setattr("portal.services.kpi.account_snapshot", lambda config: {"equity": 1000, "source": "fixture"})

    ctx = trading_kpi_context(
        tmp_path,
        positions={
            "summary": {
                "position_count": 6,
                "position_market_value": 6243,
                "position_net_market_value": 1247,
                "position_unrealized_pl": 101.8,
                "position_unrealized_plpc": 0.0164,
            }
        },
        basket={"counts": {"submitted": 0, "filled": 0}},
        queue={"counts": {"total": 6, "action_required": 1}},
    )

    cards = {card["label"]: card for card in ctx["cards"]}
    assert cards["Pending Decisions"]["value"] == "1"
    assert cards["Pending Decisions"]["alert"] is True


def test_trading_context_exposes_expanded_candidate_shortlist(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
        [
            {
                "candidate_rank": rank,
                "symbol": f"T{rank:03d}",
                "trade_action": "Long" if rank % 2 else "Short",
                "trade_quality_status": "approved",
                "risk_adjusted_score": 200 - rank,
            }
            for rank in range(1, 151)
        ],
    )

    ctx = trading_context(tmp_path)

    assert ctx["candidate_pool_count"] == 150
    assert ctx["candidate_pool_display_count"] == 150
    assert len(ctx["candidate_pool_rows"]) == 150
    assert ctx["candidate_pool_rows"][0]["symbol"] == "T001"
    assert ctx["candidate_pool_rows"][-1]["symbol"] == "T150"


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
                "symbol": "STOP",
                "decision": "close",
                "recommended_action": "close_position",
                "decision_reason": "stop_loss_triggered",
                "unrealized_pl": -20.0,
                "unrealized_plpc": -0.035,
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
    assert rows["FWRD"]["operator_call_label"] == "Review close"
    assert rows["FWRD"]["operator_apply_enabled"] is False
    assert rows["STOP"]["decision"] == "close_candidate"
    assert rows["STOP"]["operator_call_label"] == "Auto close"
    assert rows["STOP"]["action_button_label"] == "Auto managed"
    assert rows["FRMI"]["operator_call_label"] == "Review concentration"
    assert rows["FRMI"]["operator_apply_enabled"] is False
    assert rows["GLIBK"]["operator_call_label"] == "Hold - logic check"
    assert rows["GLIBK"]["operator_apply_enabled"] is False


def test_action_queue_counts_only_rows_requiring_attention(tmp_path):
    write_csv(
        tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [
            {
                "symbol": "WIN",
                "decision": "replace",
                "recommended_action": "close_then_open_replacement",
                "decision_reason": "signal_stale|replacement_rank_improvement",
                "unrealized_pl": 15.0,
                "unrealized_plpc": 0.025,
            },
            {
                "symbol": "WATCH",
                "decision": "watch",
                "recommended_action": "rescore_before_add_or_hold",
                "decision_reason": "signal_stale|no_eligible_replacement_available",
                "unrealized_pl": -2.0,
                "unrealized_plpc": -0.005,
            },
            {
                "symbol": "REVIEW",
                "decision": "replace",
                "recommended_action": "close_then_open_replacement",
                "decision_reason": "signal_stale|replacement_rank_improvement",
                "unrealized_pl": -3.0,
                "unrealized_plpc": -0.01,
            },
        ],
    )

    ctx = action_queue_context(tmp_path)
    rows = {row["symbol"]: row for row in ctx["items"]}

    assert ctx["counts"]["total"] == 3
    assert ctx["counts"]["action_required"] == 1
    assert rows["WIN"]["operator_call_label"] == "Hold winner"
    assert rows["WATCH"]["operator_call_label"] == "Watch only"
    assert rows["WATCH"]["operator_explanation"] == "Loss is visible but remains below the manager close trigger."
    assert rows["REVIEW"]["operator_call_label"] == "Manual review"


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


def test_positions_context_basket_return_matches_gross_summary_return(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [
            {"symbol": "LONG", "qty": 2, "market_value": 110, "cost_basis": 100, "unrealized_pl": 10, "unrealized_plpc": 0.1},
            {"symbol": "SHORT", "qty": -2, "market_value": -90, "cost_basis": -100, "unrealized_pl": 10, "unrealized_plpc": 0.1},
        ],
    )

    ctx = positions_context(tmp_path)

    assert ctx["summary"]["position_unrealized_plpc"] == 0.1
    assert ctx["basket_return"] == ctx["summary"]["position_unrealized_plpc"]


def test_positions_context_reconciles_stale_held_overnight_banner(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [
            {"symbol": "AAA", "qty": 1, "market_value": 100, "cost_basis": 100},
            {"symbol": "BBB", "qty": 1, "market_value": 100, "cost_basis": 100},
        ],
    )
    state_path = tmp_path / "data" / "portal_outputs" / "paper_autopilot_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"eod_state":"postclose","eod_banner":"Held overnight: 8 positions did not flatten."}', encoding="utf-8")

    ctx = positions_context(tmp_path)

    assert ctx["eod_banner"] == "Held overnight: 2 positions did not flatten."


def test_positions_context_clears_held_overnight_banner_when_flat(tmp_path):
    state_path = tmp_path / "data" / "portal_outputs" / "paper_autopilot_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"eod_state":"postclose","eod_banner":"Held overnight: 8 positions did not flatten."}', encoding="utf-8")

    ctx = positions_context(tmp_path)

    assert ctx["eod_banner"] == ""


def test_positions_context_surfaces_pending_operator_close_without_tracking_match(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "SHORTY", "qty": -5, "market_value": -50, "cost_basis": -55}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "operator_actions" / "operator_position_actions_1.csv",
        [
            {
                "timestamp": "2026-06-10T13:00:35",
                "symbol": "SHORTY",
                "operator_action": "close",
                "status": "submitted",
                "alpaca_status": "pending_new",
                "order_id": "close-shorty",
                "client_order_id": "stockml-close-shorty",
            }
        ],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_tracking_1.csv",
        [{"symbol": "OTHER", "status": "submitted", "alpaca_status": "new", "order_id": "other"}],
    )

    ctx = positions_context(tmp_path)

    assert ctx["pending_close_order_count"] == 1
    assert ctx["positions"][0]["status"] == "submitted"
    assert ctx["positions"][0]["broker_order"]["order_id"] == "close-shorty"


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


def test_positions_context_adds_holding_review_fields(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "BNY", "qty": 1, "market_value": 150.35, "cost_basis": 150, "unrealized_pl": 0.35, "unrealized_plpc": 0.00231}],
    )
    write_csv(
        tmp_path / "data" / "trading" / "holding_period" / "holding_review_1.csv",
        [
            {
                "symbol": "BNY",
                "trading_stream": "multi_day",
                "recommended_holding_days": 10,
                "review_after_days": 2,
                "max_holding_days": 5,
                "holding_quality": "watch",
                "holding_gate_pass": True,
                "holding_gate_reason": "positive_holding_edge_watch",
            }
        ],
    )

    ctx = positions_context(tmp_path)
    row = ctx["positions"][0]

    assert row["holding_review_status"] == "available"
    assert row["trading_stream"] == "multi_day"
    assert row["recommended_holding_days"] == 10
    assert row["review_after_days"] == 2
    assert row["max_holding_days"] == 5
    assert row["holding_quality"] == "watch"


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


def test_action_queue_marks_rotation_as_automatic_when_operator_confirm_disabled(monkeypatch, tmp_path):
    write_csv(tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv", [{"symbol": "CSTL", "qty": 10}])

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
    monkeypatch.setattr(
        "portal.services.trading_api_service.load_rotation_config",
        lambda: type("RotationCfg", (), {"enabled": True, "require_operator_confirm": False})(),
    )

    ctx = action_queue_context(tmp_path)
    row = ctx["items"][0]

    assert row["operator_call_label"] == "Auto rotation"
    assert row["operator_apply_enabled"] is False
    assert "automatically" in row["operator_call_reason"]
