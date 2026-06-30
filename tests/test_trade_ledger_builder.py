from datetime import datetime, timezone

from stockml.diagnostics.trade_ledger_builder import build_trade_ledger_from_events


def ev(event_type, **kwargs):
    base = {"id": kwargs.pop("id", 1), "event_at": kwargs.pop("event_at", datetime(2026, 6, 30, 14, 0, tzinfo=timezone.utc)), "event_type": event_type, "source": kwargs.pop("source", "test")}
    base.update(kwargs)
    return base


def test_submitted_order_without_fill_does_not_create_trade():
    result = build_trade_ledger_from_events([ev("candidate_submitted", symbol="AAA", client_order_id="cid-1", broker_order_id="oid-1")])
    assert result.ledger.empty
    assert result.summary["submitted_orders"] == 1
    assert result.summary["fit_for_attribution_decision"] == "NOT_FIT_NO_TRADES"


def test_cancelled_order_does_not_create_trade():
    result = build_trade_ledger_from_events([ev("filled", symbol="AAA", status="cancelled", broker_order_id="oid-1", filled_avg_price=10, filled_qty=1)])
    assert result.ledger.empty
    assert result.summary["cancelled_orders"] == 1


def test_opening_fill_creates_trade_row():
    result = build_trade_ledger_from_events([ev("filled", symbol="AAA", side="buy", order_intent="open_long", broker_order_id="oid-1", client_order_id="cid-1", filled_avg_price=10, filled_qty=3)])
    row = result.ledger.iloc[0]
    assert row["trade_id"] == "trade-oid-1"
    assert row["position_id"] == "position-oid-1"
    assert row["position_status"] == "open"


def test_long_realised_pnl_is_correct():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="buy", order_intent="open_long", broker_order_id="oid-1", client_order_id="cid-1", trade_id="trade-1", position_id="pos-1", filled_avg_price=10, filled_qty=5),
        ev("close_filled", id=2, symbol="AAA", order_intent="close_long", broker_order_id="oid-2", trade_id="trade-1", position_id="pos-1", filled_avg_price=12, filled_qty=5),
    ])
    assert result.ledger.iloc[0]["realised_pnl"] == 10
    assert result.ledger.iloc[0]["position_status"] == "closed"


def test_short_realised_pnl_is_correct():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="sell", order_intent="open_short", broker_order_id="oid-1", trade_id="trade-1", position_id="pos-1", filled_avg_price=10, filled_qty=5),
        ev("close_filled", id=2, symbol="AAA", order_intent="cover_short", broker_order_id="oid-2", trade_id="trade-1", position_id="pos-1", filled_avg_price=8, filled_qty=5),
    ])
    assert result.ledger.iloc[0]["realised_pnl"] == 10


def test_open_long_unrealised_pnl_is_correct():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="buy", order_intent="open_long", broker_order_id="oid-1", trade_id="trade-1", position_id="pos-1", filled_avg_price=10, filled_qty=5),
        ev("monitor_watch", id=2, symbol="AAA", trade_id="trade-1", position_id="pos-1", current_price=11),
    ])
    assert result.ledger.iloc[0]["unrealised_pnl"] == 5


def test_open_short_unrealised_pnl_is_correct():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="sell", order_intent="open_short", broker_order_id="oid-1", trade_id="trade-1", position_id="pos-1", filled_avg_price=10, filled_qty=5),
        ev("monitor_watch", id=2, symbol="AAA", trade_id="trade-1", position_id="pos-1", current_price=9),
    ])
    assert result.ledger.iloc[0]["unrealised_pnl"] == 5


def test_close_fill_links_to_existing_trade_id():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="buy", broker_order_id="oid-1", trade_id="trade-1", position_id="pos-1", filled_avg_price=10, filled_qty=1),
        ev("close_filled", id=2, symbol="AAA", broker_order_id="oid-2", trade_id="trade-1", position_id="pos-1", filled_avg_price=9, filled_qty=1),
    ])
    assert len(result.ledger) == 1
    assert result.ledger.iloc[0]["exit_broker_order_id"] == "oid-2"


def test_same_symbol_separate_trades_remain_separate():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="buy", broker_order_id="oid-1", trade_id="trade-1", filled_avg_price=10, filled_qty=1),
        ev("filled", id=2, symbol="AAA", side="buy", broker_order_id="oid-2", trade_id="trade-2", filled_avg_price=11, filled_qty=1),
    ])
    assert set(result.ledger["trade_id"]) == {"trade-1", "trade-2"}


def test_symbol_only_fallback_is_marked_low_confidence():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="buy", filled_avg_price=10, filled_qty=1),
        ev("close_filled", id=2, symbol="AAA", filled_avg_price=11, filled_qty=1),
    ])
    assert result.ledger.iloc[0]["lineage_quality"] == "low"
    assert "symbol_time_fallback_used" in result.ledger.iloc[0]["lineage_warnings"]


def test_missing_entry_price_marks_insufficient_data():
    result = build_trade_ledger_from_events([ev("filled", symbol="AAA", side="buy", broker_order_id="oid-1", filled_qty=1)])
    assert result.ledger.iloc[0]["position_status"] == "insufficient_data"


def test_missing_current_price_for_open_trade_adds_warning():
    result = build_trade_ledger_from_events([ev("filled", symbol="AAA", side="buy", broker_order_id="oid-1", filled_avg_price=10, filled_qty=1)])
    assert "missing_current_price" in result.ledger.iloc[0]["lineage_warnings"]


def test_unmatched_close_event_is_written_to_unmatched_report():
    result = build_trade_ledger_from_events([ev("close_filled", symbol="AAA", broker_order_id="oid-2", filled_avg_price=11, filled_qty=1)])
    assert result.unmatched.iloc[0]["reason_unmatched"] == "orphan_close_fill"


def test_summary_returns_not_fit_no_trades_when_no_fills_exist():
    result = build_trade_ledger_from_events([ev("candidate_scanned", symbol="AAA")])
    assert result.summary["fit_for_attribution_decision"] == "NOT_FIT_NO_TRADES"


def test_summary_returns_fit_for_attribution_when_valid_closed_trade_exists():
    result = build_trade_ledger_from_events([
        ev("filled", id=1, symbol="AAA", side="buy", broker_order_id="oid-1", trade_id="trade-1", position_id="pos-1", filled_avg_price=10, filled_qty=1),
        ev("close_filled", id=2, symbol="AAA", broker_order_id="oid-2", trade_id="trade-1", position_id="pos-1", filled_avg_price=12, filled_qty=1),
    ])
    assert result.summary["fit_for_attribution_decision"] == "FIT_FOR_ATTRIBUTION"
