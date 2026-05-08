import pandas as pd

from stockml.trading.trade_journal import build_trade_journal, lifecycle_state


def test_trade_journal_marks_rejected_rows():
    plan = pd.DataFrame(
        [
            {
                "symbol": "AKAN",
                "trade_quality_status": "rejected",
                "trade_quality_reason": "market_cap_below_minimum",
                "approved_notional": 0,
                "suggested_quantity": 0,
            }
        ]
    )
    journal = build_trade_journal(plan)
    assert journal.iloc[0]["lifecycle_state"] == "risk_rejected"
    assert journal.iloc[0]["readable_reason"] == "Market cap below minimum"


def test_trade_journal_marks_dry_run_as_order_planned():
    plan = pd.DataFrame([{"symbol": "FLEX", "trade_quality_status": "approved", "approved_notional": 1000, "suggested_quantity": 7}])
    results = pd.DataFrame([{"symbol": "FLEX", "status": "dry_run", "message": "disabled"}])
    journal = build_trade_journal(plan, results)
    assert journal.iloc[0]["lifecycle_state"] == "order_planned"


def test_lifecycle_state_detects_filled_order():
    row = pd.Series({"trade_quality_status": "approved", "status": "submitted", "alpaca_status": "filled", "filled_qty": 2})
    assert lifecycle_state(row) == "order_filled"
