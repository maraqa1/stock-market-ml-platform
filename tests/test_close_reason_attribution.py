from stockml.reports.closed_trade_metrics import classify_close_reason


def test_close_reason_is_not_eod_flatten_for_snapshot_reconstruction():
    assert classify_close_reason("snapshot_flattened", {"trigger_source": "position_snapshot_reconstruction"}) == "OTHER"


def test_close_reason_eod_flatten_is_preserved_for_real_eod_trigger():
    assert classify_close_reason("eod_flatten") == "EOD_FLATTEN"
