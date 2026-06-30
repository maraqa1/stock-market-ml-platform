import pandas as pd

from stockml.diagnostics.position_management_outcomes import build_position_management_outcomes


def test_position_management_outcomes_groups_take_profit_and_stop_loss():
    ledger = pd.DataFrame([
        {"trade_id": "t1", "symbol": "AAA", "side": "long", "position_status": "closed", "realised_pnl": 12, "realised_return_pct": 1.2, "exit_reason": "take_profit", "holding_minutes": 50},
        {"trade_id": "t2", "symbol": "BBB", "side": "long", "position_status": "closed", "realised_pnl": -8, "realised_return_pct": -0.8, "exit_reason": "stop_loss", "holding_minutes": 20},
    ])
    result = build_position_management_outcomes(ledger)
    assert result.summary["status"] == "ok"
    assert set(result.summary_frame["management_action_family"]) == {"profit_exit", "risk_exit"}
    assert result.summary["winner_rows"] == 1
    assert result.summary["loser_rows"] == 1


def test_position_management_outcomes_open_trade_uses_unrealised_pnl():
    ledger = pd.DataFrame([{"trade_id": "t1", "symbol": "AAA", "side": "long", "position_status": "open", "unrealised_pnl": 5, "unrealised_return_pct": 0.5}])
    result = build_position_management_outcomes(ledger)
    row = result.frame.iloc[0]
    assert row["exit_reason"] == "open"
    assert row["outcome_bucket"] == "open_winner"
    assert row["pnl_usd"] == 5


def test_position_management_outcomes_missing_exit_reason_becomes_unknown():
    ledger = pd.DataFrame([{"trade_id": "t1", "symbol": "AAA", "side": "long", "position_status": "closed", "realised_pnl": 0}])
    result = build_position_management_outcomes(ledger)
    assert result.frame.iloc[0]["exit_reason"] == "unknown"
    assert result.frame.iloc[0]["management_action_family"] == "unknown"


def test_position_management_outcomes_empty_ledger_is_insufficient_data_with_stable_schema():
    result = build_position_management_outcomes(pd.DataFrame())
    expected = [
        "status", "trade_id", "position_id", "symbol", "side", "position_status", "entry_time", "exit_time", "holding_minutes",
        "exit_reason", "outcome_bucket", "pnl_usd", "return_pct", "management_action_family", "lineage_quality", "lineage_warnings", "diagnostic_note",
    ]
    assert list(result.frame.columns) == expected
    assert result.summary["status"] == "insufficient_data"
    assert result.summary_frame.iloc[0]["trade_count"] == 0
