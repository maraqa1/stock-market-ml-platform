from stockml.trading.position_sizing import approved_notional, suggested_quantity


def test_notional_by_risk_tier():
    assert approved_notional(1000, "large_liquid") == 1000
    assert approved_notional(1000, "mid_risk") == 500
    assert approved_notional(1000, "speculative") == 250
    assert approved_notional(1000, "reject") == 0


def test_quantity_floors_notional_by_current_price():
    assert suggested_quantity(1000, 33.4) == 29
    assert suggested_quantity(0, 33.4) == 0
    assert suggested_quantity(1000, 0) == 0
