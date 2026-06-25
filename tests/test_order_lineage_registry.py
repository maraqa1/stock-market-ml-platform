from stockml.trading.order_lineage_registry import OrderLineageRegistry


def test_registry_links_candidate_client_broker_and_fill():
    registry = OrderLineageRegistry()
    registry.register_selected({"candidate_id": "cand-1", "client_order_id": "cid-1", "symbol": "AAA"})
    submitted = registry.register_submitted({"candidate_id": "cand-1", "client_order_id": "cid-1", "symbol": "AAA"}, broker_order_id="oid-1")
    assert submitted.position_id == ""
    assert submitted.trade_id == ""
    filled = registry.register_fill({"client_order_id": "cid-1", "broker_order_id": "oid-1", "symbol": "AAA"})
    assert registry.lookup({"candidate_id": "cand-1"}) is filled
    assert registry.lookup({"client_order_id": "cid-1"}) is filled
    assert registry.lookup({"broker_order_id": "oid-1"}) is filled
    assert filled.position_id == "position-oid-1"
    assert filled.trade_id == "trade-oid-1"


def test_registry_does_not_invent_trade_id_without_broker_fill():
    registry = OrderLineageRegistry()
    entry = registry.register_submitted({"candidate_id": "cand-2", "client_order_id": "cid-2", "symbol": "BBB"})
    assert entry.trade_id == ""
    assert entry.position_id == ""
