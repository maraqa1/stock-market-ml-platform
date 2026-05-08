from stockml.trading.order_builder import bracket_order_payload, validate_order_payload


def test_valid_market_order():
    order = {"symbol": "FLEX", "side": "buy", "type": "market", "time_in_force": "day", "qty": "2"}
    assert validate_order_payload(order).valid is True


def test_invalid_both_qty_and_notional():
    order = {"symbol": "FLEX", "side": "buy", "type": "market", "time_in_force": "day", "qty": "2", "notional": 100}
    result = validate_order_payload(order)
    assert result.valid is False
    assert result.reason == "both_qty_and_notional"


def test_missing_limit_price_on_limit_order():
    order = {"symbol": "FLEX", "side": "buy", "type": "limit", "time_in_force": "day", "qty": "2"}
    assert validate_order_payload(order).reason == "missing_limit_price"


def test_missing_stop_price_on_stop_order():
    order = {"symbol": "FLEX", "side": "buy", "type": "stop", "time_in_force": "day", "qty": "2"}
    assert validate_order_payload(order).reason == "missing_stop_price"


def test_bracket_order_payload_for_long():
    payload = bracket_order_payload("FLEX", "buy", 2, "market", "day", 147.34, 134.83, "cid")
    assert payload["order_class"] == "bracket"
    assert payload["take_profit"]["limit_price"] == 147.34
    assert payload["stop_loss"]["stop_price"] == 134.83
    assert validate_order_payload(payload).valid is True
