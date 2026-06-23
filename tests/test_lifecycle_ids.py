from stockml.trading.lifecycle_ids import (
    candidate_lineage,
    derive_lineage_order_intent,
    fill_lineage,
    order_lineage,
    trade_id_for,
)


def test_candidate_scan_and_candidate_block_share_candidate_id():
    scan = candidate_lineage(symbol="AAA", cycle_id="cycle-1", pipeline_run_id="run-1", candidate_source="scan", model_version="model-a", side="buy")
    block = candidate_lineage(symbol="AAA", cycle_id="cycle-1", pipeline_run_id="run-1", candidate_source="scan", model_version="model-a", side="buy")
    assert scan.values["candidate_id"] == block.values["candidate_id"]
    assert scan.values["event_key"] == block.values["event_key"]


def test_no_id_collision_across_cycles():
    first = candidate_lineage(symbol="AAA", cycle_id="cycle-1", candidate_source="scan")
    second = candidate_lineage(symbol="AAA", cycle_id="cycle-2", candidate_source="scan")
    assert first.values["candidate_id"] != second.values["candidate_id"]


def test_submitted_and_filled_events_share_client_order_id():
    candidate = candidate_lineage(symbol="AAA", cycle_id="cycle-1", candidate_source="scan", side="buy", client_order_id="cid-1")
    submitted = order_lineage({**candidate.values, "symbol": "AAA", "client_order_id": "cid-1", "order_intent": "open_long"}, broker_order_id="oid-1")
    filled = fill_lineage({**submitted.values, "symbol": "AAA", "client_order_id": "cid-1", "order_id": "oid-1"})
    assert submitted.values["client_order_id"] == "cid-1"
    assert filled.values["client_order_id"] == "cid-1"
    assert filled.values["broker_order_id"] == "oid-1"


def test_open_and_close_events_share_trade_id_when_evidence_is_present():
    opened = trade_id_for(symbol="AAA", broker_order_id="open-1", client_order_id="cid-open")
    closed = trade_id_for(symbol="AAA", client_order_id="cid-close", existing_trade_id=opened)
    assert opened
    assert closed == opened


def test_open_long_and_open_short_intent_derivation_is_correct():
    assert derive_lineage_order_intent(current_qty=0, attempted_side="buy", attempted_qty=10) == "open_long"
    assert derive_lineage_order_intent(current_qty=0, attempted_side="sell", attempted_qty=10) == "open_short"


def test_close_long_and_cover_short_intent_derivation_is_correct():
    assert derive_lineage_order_intent(current_qty=10, attempted_side="sell", attempted_qty=10) == "close_long"
    assert derive_lineage_order_intent(current_qty=-10, attempted_side="buy", attempted_qty=10) == "cover_short"


def test_missing_evidence_produces_lineage_warning():
    lineage = candidate_lineage(symbol="", cycle_id="", candidate_source="scan")
    assert "missing_cycle_id" in lineage.values["lineage_warning"]
    assert "missing_candidate_id" in lineage.values["lineage_warning"]


def test_two_apps_trades_receive_distinct_position_and_trade_ids():
    first = fill_lineage({"symbol": "APPS", "client_order_id": "cid-1", "order_id": "oid-1", "order_intent": "open_long"})
    second = fill_lineage({"symbol": "APPS", "client_order_id": "cid-2", "order_id": "oid-2", "order_intent": "open_long"})
    assert first.values["position_id"] == "position-oid-1"
    assert second.values["position_id"] == "position-oid-2"
    assert first.values["trade_id"] == "trade-oid-1"
    assert second.values["trade_id"] == "trade-oid-2"
    assert first.values["position_id"] != second.values["position_id"]
    assert not first.values["position_id"].startswith("paper:")
