from pathlib import Path

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.near_miss_analysis import OUTPUT_COLUMNS, near_miss_rows, write_near_miss_analysis


def config() -> AlpacaConfig:
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=False,
        extended_hours=False,
        max_orders=10,
        max_notional_per_order=1000,
        max_total_notional=10000,
        min_trade_price=5,
        max_sector_fraction=0.4,
        min_side_probability=0.55,
        min_abs_probability_edge=0.05,
        min_intraday_volume=100000,
        min_market_cap=300_000_000,
        min_risk_adjusted_score=0.005,
        transaction_cost_bps=10,
        min_avg_dollar_volume_20d=5_000_000,
        min_expected_trade_return=0.002,
    )


def base_row(**overrides):
    row = {
        "symbol": "AAA",
        "side": "buy",
        "trade_action": "Long",
        "trade_quality_status": "rejected",
        "candidate_rank": 7,
        "side_probability": 0.7,
        "probability_edge": 0.2,
        "expected_trade_return": 0.02,
        "risk_adjusted_score": 0.02,
        "current_price": 10,
        "market_cap": 1_000_000_000,
        "avg_dollar_volume_20d": 50_000_000,
        "volatility_20d": 0.03,
        "risk_tier": "medium",
        "liquidity_tier": "high",
        "volatility_tier": "low",
    }
    row.update(overrides)
    return row


def test_expected_return_near_threshold_is_near_miss():
    frame = pd.DataFrame([base_row(expected_trade_return=0.00185, trade_quality_reason="expected_trade_return_below_threshold")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "expected_trade_return_below_threshold"
    assert row["severity"] == "near_miss"
    assert round(float(row["distance_pct"]), 4) == 0.075


def test_short_expected_return_near_threshold_uses_directional_magnitude():
    frame = pd.DataFrame(
        [
            base_row(
                side="sell",
                trade_action="Short",
                expected_trade_return=-0.0019,
                trade_quality_reason="expected_trade_return_below_threshold",
            )
        ]
    )

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "expected_trade_return_below_threshold"
    assert row["actual_value"] == 0.0019
    assert row["severity"] == "near_miss"
    assert round(float(row["distance_pct"]), 4) == 0.05


def test_expected_return_far_below_threshold_is_hard_fail():
    frame = pd.DataFrame([base_row(expected_trade_return=0.001, trade_quality_reason="expected_trade_return_below_threshold")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "expected_trade_return_below_threshold"
    assert row["severity"] == "hard_fail"


def test_readable_reasons_are_mapped_to_supported_gates():
    frame = pd.DataFrame([base_row(expected_trade_return=0.0019, trade_quality_reason="Expected return below threshold")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "expected_trade_return_below_threshold"
    assert row["severity"] == "near_miss"


def test_risk_adjusted_score_just_below_threshold_is_near_miss():
    frame = pd.DataFrame([base_row(risk_adjusted_score=0.00475, trade_quality_reason="risk_adjusted_score_below_threshold")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "risk_adjusted_score_below_threshold"
    assert row["severity"] == "near_miss"


def test_short_risk_adjusted_score_near_threshold_uses_directional_magnitude():
    frame = pd.DataFrame(
        [
            base_row(
                side="sell",
                trade_action="Short",
                risk_adjusted_score=-0.00475,
                trade_quality_reason="risk_adjusted_score_below_threshold",
            )
        ]
    )

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "risk_adjusted_score_below_threshold"
    assert row["actual_value"] == 0.00475
    assert row["severity"] == "near_miss"


def test_risk_adjusted_score_far_below_threshold_is_hard_fail():
    frame = pd.DataFrame([base_row(risk_adjusted_score=0.001, trade_quality_reason="risk_adjusted_score_below_threshold")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "risk_adjusted_score_below_threshold"
    assert row["severity"] == "hard_fail"


def test_market_cap_just_below_threshold_is_near_miss():
    frame = pd.DataFrame([base_row(market_cap=285_000_000, trade_quality_reason="market_cap_below_minimum")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "market_cap_below_minimum"
    assert row["severity"] == "near_miss"


def test_unknown_reason_is_safely_handled():
    frame = pd.DataFrame([base_row(symbol="UNK", trade_quality_reason="some_new_reason")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["symbol"] == "UNK"
    assert row["failed_gate"] == "unknown"
    assert row["failed_gate_label"] == "Unknown reason"
    assert row["severity"] == "unknown"


def test_price_far_below_threshold_is_hard_fail():
    frame = pd.DataFrame([base_row(current_price=2.5, trade_quality_reason="price_below_minimum")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "price_below_minimum"
    assert row["severity"] == "hard_fail"


def test_missing_actual_or_required_values_produce_unknown_severity():
    frame = pd.DataFrame([base_row(current_price="", trade_quality_reason="price_below_minimum")])

    result = near_miss_rows([frame], config())

    row = result.iloc[0]
    assert row["failed_gate"] == "price_below_minimum"
    assert row["actual_value"] is None
    assert row["severity"] == "unknown"


def test_near_miss_analysis_never_marks_candidates_trade_eligible():
    frame = pd.DataFrame(
        [
            base_row(symbol="APP", trade_quality_status="approved", order_eligible=True, trade_quality_reason="approved"),
            base_row(symbol="REJ", trade_quality_status="rejected", order_eligible=False, trade_quality_reason="price_below_minimum", current_price=4.5),
        ]
    )

    result = near_miss_rows([frame], config())

    assert "order_eligible" not in result.columns
    assert result["symbol"].tolist() == ["REJ"]
    assert result.iloc[0]["status"] == "rejected"


def test_output_schema_is_stable(tmp_path: Path):
    frame = near_miss_rows(
        [pd.DataFrame([base_row(trade_quality_reason="price_below_minimum", current_price=4.8)])],
        config(),
    )

    assert list(frame.columns) == OUTPUT_COLUMNS
    path = write_near_miss_analysis(frame, output_dir=tmp_path, stamp="20260513_120000")
    written = pd.read_csv(path)
    assert list(written.columns) == OUTPUT_COLUMNS
    assert path.name == "near_miss_20260513_120000.csv"
