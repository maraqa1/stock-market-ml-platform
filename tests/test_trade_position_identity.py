from stockml.trading.lifecycle_ids import candidate_lineage, order_lineage, fill_lineage


def test_candidate_lineage_does_not_use_symbol_as_position_identity():
    lineage = candidate_lineage(symbol="AAPL", cycle_id="cycle-1", side="buy")
    assert lineage.values["candidate_id"]
    assert lineage.values["position_id"] is None
    assert lineage.values["trade_id"] is None


def test_submitted_order_uses_opening_broker_order_for_position_and_trade_ids():
    candidate = candidate_lineage(symbol="AAPL", cycle_id="cycle-1", side="buy", client_order_id="cid-1").values
    submitted = order_lineage({**candidate, "symbol": "AAPL"}, broker_order_id="oid-1")
    assert submitted.values["broker_order_id"] == "oid-1"
    assert submitted.values["position_id"] == "position-oid-1"
    assert submitted.values["trade_id"] == "trade-oid-1"
    assert submitted.values["lineage_warning"] == ""


def test_fill_retains_submitted_position_and_trade_identity():
    tracked = {
        "symbol": "AAPL",
        "cycle_id": "cycle-1",
        "candidate_id": "cand-1",
        "client_order_id": "cid-1",
        "order_id": "oid-1",
        "position_id": "paper:AAPL",
        "session_mode": "regular",
    }
    filled = fill_lineage(tracked)
    assert filled.values["position_id"] == "position-oid-1"
    assert filled.values["trade_id"] == "trade-oid-1"
    assert filled.values["session_mode"] == "regular_session"
    assert "inconsistent_session_mode" in filled.values["lineage_warning"]
