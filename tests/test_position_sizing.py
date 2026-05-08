from stockml.trading.position_sizing import approved_notional, base_notional, suggested_quantity


def test_notional_by_risk_tier_and_confidence():
    assert approved_notional(1000, "high_quality", 0.80) == 1000
    assert approved_notional(1000, "medium", 0.80) == 500
    assert approved_notional(1000, "speculative", 0.80) == 250
    assert approved_notional(1000, "reject", 0.80) == 0
    assert approved_notional(1000, "high_quality", 0.61) == 750


def test_base_notional_uses_account_and_basket_caps():
    assert base_notional(100_000, 0.03, 10_000, 10) == 1000
    assert base_notional(10_000, 0.03, 10_000, 10) == 300


def test_quantity_floors_notional_by_current_price():
    assert suggested_quantity(1000, 33.4) == 29
    assert suggested_quantity(0, 33.4) == 0
    assert suggested_quantity(1000, 0) == 0
