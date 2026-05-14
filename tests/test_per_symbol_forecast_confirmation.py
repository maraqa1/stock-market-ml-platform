from stockml.trading.per_symbol_forecast.confirmation import confirmation_fields, side_alignment


def test_confirmed_when_side_magnitude_profitability_and_risk_reward_align():
    row = {
        "side": "buy",
        "current_trade_action": "Long",
        "direction_context": "long_bias",
        "expected_move_bps": 100,
        "expected_profitability_score": 120,
        "suggested_stop_bps": 80,
        "suggested_take_profit_bps": 120,
    }

    result = confirmation_fields(row)

    assert result["side_alignment"] == "aligned"
    assert result["forecast_confirmation"] == "confirmed"
    assert result["confirmation_score"] == 100


def test_conflicted_when_forecast_direction_opposes_side():
    row = {
        "side": "buy",
        "current_trade_action": "Long",
        "direction_context": "short_bias",
        "expected_move_bps": 100,
        "expected_profitability_score": 120,
        "suggested_stop_bps": 80,
        "suggested_take_profit_bps": 120,
    }

    result = confirmation_fields(row)

    assert side_alignment(row) == "conflicted"
    assert result["forecast_confirmation"] == "conflicted"


def test_insufficient_data_when_context_is_missing():
    result = confirmation_fields({"symbol": "AAPL"})

    assert result["forecast_confirmation"] == "insufficient_data"
    assert result["side_alignment"] == "unknown"
