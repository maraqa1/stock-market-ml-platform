from stockml.trading.lifecycle_ids import fill_lineage


def test_opening_fill_creates_position_and_trade_from_broker_order():
    lineage = fill_lineage({"symbol": "AAA", "candidate_id": "cand-1", "client_order_id": "cid-1", "broker_order_id": "oid-1", "order_intent": "open_long"})
    assert lineage.values["position_id"] == "position-oid-1"
    assert lineage.values["trade_id"] == "trade-oid-1"
    assert lineage.values["lineage_warning"] == ""


def test_paper_symbol_position_id_is_not_retained_on_fill():
    lineage = fill_lineage({"symbol": "AAA", "client_order_id": "cid-2", "broker_order_id": "oid-2", "position_id": "paper:AAA"})
    assert lineage.values["position_id"] == "position-oid-2"
