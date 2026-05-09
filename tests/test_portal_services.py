from pathlib import Path
import time

import pandas as pd

from portal.services.latest_file_reader import latest_file, readable_reason
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
