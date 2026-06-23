from stockml.trading.lifecycle_ids import exit_lineage, monitor_lineage


def test_exit_lineage_generates_exit_decision_id_with_trade_linkage():
    lineage = exit_lineage(
        {
            "symbol": "AAPL",
            "cycle_id": "cycle-1",
            "position_id": "position-oid-1",
            "trade_id": "trade-oid-1",
            "session_mode": "overnight",
        },
        reason="hard_stop_hit",
    )
    assert lineage.values["exit_decision_id"].startswith("exit-")
    assert lineage.values["position_id"] == "position-oid-1"
    assert lineage.values["trade_id"] == "trade-oid-1"
    assert lineage.values["session_mode"] == "overnight_24_5"


def test_exit_lineage_warns_when_trade_evidence_is_missing():
    lineage = exit_lineage({"symbol": "AAPL", "session_mode": "regular_session"}, reason="manual_close")
    assert "missing_position_id" in lineage.values["lineage_warning"]
    assert "missing_trade_id" in lineage.values["lineage_warning"]
    assert "missing_exit_decision_id" in lineage.values["lineage_warning"]


def test_monitor_lineage_requires_position_and_trade_ids():
    lineage = monitor_lineage({"symbol": "AAPL", "position_id": "position-oid-1"})
    assert "missing_trade_id" in lineage.values["lineage_warning"]
