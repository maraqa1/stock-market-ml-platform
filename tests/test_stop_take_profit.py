from stockml.trading.stop_take_profit import stop_take_profit_prices


def test_default_long_stop_and_take_profit():
    levels = stop_take_profit_prices(100, "buy", "low")
    assert levels["stop_loss_price"] == 97
    assert levels["take_profit_price"] == 106
    assert levels["max_holding_days"] == 5


def test_high_volatility_uses_wider_levels():
    levels = stop_take_profit_prices(100, "buy", "high")
    assert levels["stop_loss_price"] == 95
    assert levels["take_profit_price"] == 110
    assert levels["max_holding_days"] == 10


def test_short_levels_are_reversed():
    levels = stop_take_profit_prices(100, "sell", "low")
    assert levels["stop_loss_price"] == 103
    assert levels["take_profit_price"] == 94


def test_speculative_risk_uses_wider_levels():
    levels = stop_take_profit_prices(100, "buy", "low", "speculative")
    assert levels["stop_loss_price"] == 95
    assert levels["take_profit_price"] == 110
