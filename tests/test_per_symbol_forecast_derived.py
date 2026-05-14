from stockml.trading.per_symbol_forecast.derived import derived_fields


def test_derived_fields_are_pass_through_and_normalized():
    row = {
        "symbol": "aapl",
        "side": "buy",
        "trade_action": "Long",
        "candidate_rank": 3,
        "risk_adjusted_score": 0.11,
        "meta_label_probability": 0.7,
        "current_price": 190.5,
        "price_position_in_intraday_range": 0.8,
    }

    result = derived_fields(row, "2026-05-14T12:00:00+00:00")

    assert result["symbol"] == "AAPL"
    assert result["side"] == "buy"
    assert result["candidate_rank"] == 3
    assert result["model_score"] == 0.11
    assert result["intraday_range_position"] == 0.8


def test_missing_intraday_inputs_yield_null():
    result = derived_fields({"symbol": "MSFT"}, "2026-05-14T12:00:00+00:00")

    assert result["vwap_distance_bps"] is None
    assert result["spread_bps"] is None
    assert result["dollar_volume_today"] is None
