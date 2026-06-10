from pathlib import Path

import pandas as pd

from stockml.reports.held_vs_candidate import build_held_vs_candidate_diagnostic, write_held_vs_candidate_diagnostic


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _fixture(root: Path) -> None:
    _write(
        root / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [
            {"symbol": "AAA", "side": "long", "qty": 10, "avg_entry_price": 10, "current_price": 9.5, "market_value": 95, "cost_basis": 100, "unrealized_pl": -5, "unrealized_plpc": -0.05},
            {"symbol": "BBB", "side": "short", "qty": -5, "avg_entry_price": 20, "current_price": 19, "market_value": -95, "cost_basis": -100, "unrealized_pl": 5, "unrealized_plpc": 0.05},
        ],
    )
    _write(
        root / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
        [
            {"symbol": "AAA", "side": "buy", "trade_action": "Long", "trade_quality_status": "rejected", "expected_trade_return": 0.0, "risk_adjusted_score": 0.0, "candidate_rank": 20},
            {"symbol": "BBB", "side": "sell", "trade_action": "Short", "trade_quality_status": "approved", "expected_trade_return": -0.02, "risk_adjusted_score": -0.03, "candidate_rank": 4},
            {"symbol": "VPG", "side": "buy", "trade_action": "Long", "trade_quality_status": "approved", "expected_trade_return": 0.05, "risk_adjusted_score": 0.06, "side_probability": 0.8, "candidate_rank": 1, "sector": "Industrials"},
            {"symbol": "HUM", "side": "buy", "trade_action": "Long", "trade_quality_status": "reduced", "expected_trade_return": 0.04, "risk_adjusted_score": 0.05, "side_probability": 0.7, "candidate_rank": 2, "sector": "Healthcare"},
            {"symbol": "ORD", "side": "buy", "trade_action": "Long", "trade_quality_status": "approved", "expected_trade_return": 0.09, "risk_adjusted_score": 0.09, "side_probability": 0.9, "candidate_rank": 3},
        ],
    )
    _write(root / "data" / "portal_outputs" / "08_alpaca_paper_order_tracking_1.csv", [{"symbol": "ORD", "alpaca_status": "new"}])
    _write(
        root / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [
            {"symbol": "AAA", "decision": "watch", "recommended_action": "manual_review", "decision_reason": "latest_signal_unknown"},
            {"symbol": "BBB", "decision": "watch", "recommended_action": "keep_position", "decision_reason": "latest_signal_fresh"},
        ],
    )
    _write(
        root / "data" / "trading" / "holding_period" / "holding_review_1.csv",
        [
            {"symbol": "AAA", "holding_quality": "avoid", "recommended_action": "avoid", "holding_gate_reason": "holding_edge_not_confirmed"},
            {"symbol": "BBB", "holding_quality": "strong", "recommended_action": "hold", "holding_gate_reason": "positive_holding_edge"},
        ],
    )
    _write(root / "data" / "model_outputs" / "advanced_model_signal_table_1.csv", [{"ticker": "AAA", "trade_action": "No Decision"}, {"ticker": "BBB", "trade_action": "Short"}])


def test_held_vs_candidate_writes_outputs_and_excludes_held_or_open_order_symbols(tmp_path: Path):
    _fixture(tmp_path)

    outputs = write_held_vs_candidate_diagnostic(root=tmp_path, stamp="20260610_120000")

    assert outputs.position_rows == 2
    assert outputs.available_rows == 2
    assert outputs.warning_count >= 3
    assert outputs.positions_path.exists()
    assert outputs.available_path.exists()
    assert outputs.summary_path.exists()

    held = pd.read_csv(outputs.positions_path)
    available = pd.read_csv(outputs.available_path)
    aaa = held[held["symbol"].eq("AAA")].iloc[0]
    assert aaa["rotation_flag"] == "review_close"
    assert "candidate_not_currently_approved" in aaa["warnings"]
    assert "latest_signal_unknown" in aaa["warnings"]
    assert available["symbol"].tolist() == ["VPG", "HUM"]
    assert "ORD" not in set(available["symbol"])


def test_held_vs_candidate_context_payload_is_serializable(tmp_path: Path):
    _fixture(tmp_path)

    result = build_held_vs_candidate_diagnostic(root=tmp_path, write=False)

    assert result["status"] == "ok"
    assert result["summary"]["open_positions"] == 2
    assert result["held_positions"][0]["symbol"] == "AAA"
    assert result["available_candidates"][0]["symbol"] == "VPG"
