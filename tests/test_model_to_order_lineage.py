from stockml.trading.lifecycle_ids import candidate_lineage
from stockml.trading.paper_trader import _result_row


def test_selected_candidate_lineage_survives_submission_row():
    lineage = candidate_lineage(symbol="AAA", cycle_id="cycle-1", pipeline_run_id="run-1", candidate_source="paper_order_plan", side="buy", client_order_id="cid-1")
    order = {**lineage.values, "symbol": "AAA", "side": "buy", "client_order_id": "cid-1"}
    row = _result_row(order, "submitted", order_id="oid-1", response={"status": "accepted"})
    assert row["candidate_id"] == lineage.values["candidate_id"]
    assert row["client_order_id"] == "cid-1"
    assert row["broker_order_id"] == "oid-1"
    assert row["position_id"] in ("", None)
    assert row["trade_id"] in ("", None)
