import pandas as pd

from stockml.diagnostics.broker_fill_reconciliation import reconcile_orders


def test_reconciliation_marks_matched_fill_when_activity_and_ledger_exist():
    result = reconcile_orders(
        results=pd.DataFrame([{"client_order_id": "cid", "symbol": "AAA", "side": "buy", "alpaca_status": "filled", "filled_qty": 2, "filled_avg_price": 10}]),
        activity=pd.DataFrame([{"client_order_id": "cid", "event_type": "filled"}]),
        ledger=pd.DataFrame([{"client_order_id": "cid", "trade_id": "trade-1", "entry_broker_order_id": "oid"}]),
    )
    assert result.frame.iloc[0]["reconciliation_status"] == "matched_fill"
    assert result.summary["status"] == "ok"


def test_reconciliation_flags_broker_fill_missing_activity_event():
    result = reconcile_orders(
        results=pd.DataFrame([{"client_order_id": "cid", "symbol": "AAA", "side": "buy", "alpaca_status": "filled", "filled_qty": 2}]),
        activity=pd.DataFrame([]),
        ledger=pd.DataFrame([]),
    )
    assert result.frame.iloc[0]["reconciliation_status"] == "missing_activity_fill"
    assert result.summary["missing_activity_fills"] == 1
    assert result.summary["status"] == "needs_repair"


def test_reconciliation_flags_activity_fill_missing_ledger_trade():
    result = reconcile_orders(
        results=pd.DataFrame([{"client_order_id": "cid", "symbol": "AAA", "side": "buy", "alpaca_status": "filled", "filled_qty": 2}]),
        activity=pd.DataFrame([{"client_order_id": "cid", "event_type": "filled"}]),
        ledger=pd.DataFrame([]),
    )
    assert result.frame.iloc[0]["reconciliation_status"] == "missing_ledger_trade"
    assert result.summary["missing_ledger_trades"] == 1


def test_reconciliation_treats_plan_only_as_expected_no_fill():
    result = reconcile_orders(results=pd.DataFrame([{"client_order_id": "cid", "symbol": "AAA", "status": "dry_run"}]))
    assert result.frame.iloc[0]["reconciliation_status"] == "dry_run"
    assert result.summary["status"] == "ok"


def test_reconciliation_output_schema_is_stable():
    result = reconcile_orders(results=pd.DataFrame([{"client_order_id": "cid", "symbol": "AAA", "status": "new"}]))
    assert list(result.frame.columns)[:6] == ["client_order_id", "broker_order_id", "symbol", "side", "planned_status", "result_status"]
