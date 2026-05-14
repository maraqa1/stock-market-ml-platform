import pandas as pd

from stockml.trading.per_symbol_forecast.statistical import (
    calibrated_move_bps,
    direction_context,
    magnitude_bucket,
    rank_to_return_slope,
    statistical_fields,
    volatility_adjusted_score,
)


def test_rank_to_return_slope_is_positive_when_signal_exists():
    history = pd.DataFrame({"model_score": [1, 2, 3, 4], "target_return_5d": [0.01, 0.02, 0.03, 0.04]})

    assert rank_to_return_slope(history) > 0


def test_rank_to_return_slope_is_zero_for_missing_inputs():
    assert rank_to_return_slope(pd.DataFrame({"x": [1]})) == 0.0


def test_volatility_adjusted_score_handles_zero_vol():
    assert volatility_adjusted_score(0.5, 0.0) == 50.0


def test_short_invalidation_level_is_above_entry():
    row = {"symbol": "AAA", "side": "sell", "trade_action": "Short", "current_price": 100, "volatility_20d": 0.02, "risk_adjusted_score": 0.1}

    result = statistical_fields(row)

    assert result["suggested_stop_bps"] == 200.0
    assert result["invalidation_level"] == 102.0
    assert result["direction_context"] == "short_bias"


def test_direction_and_magnitude_context_are_diagnostic_not_probability():
    row = {"symbol": "AAA", "side": "buy", "trade_action": "Long"}

    assert direction_context(row, 0.01) == "long_bias"
    assert magnitude_bucket(25) == "small"
    assert magnitude_bucket(100) == "medium"
    assert magnitude_bucket(250) == "large"


def test_expected_move_is_calibrated_by_volatility_cap():
    assert calibrated_move_bps(1000, 0.02) == 300
    assert calibrated_move_bps(100, 0.02) == 100
