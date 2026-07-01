from pathlib import Path

import pandas as pd

from stockml.diagnostics.expected_return_calibration import (
    apply_expected_return_execution_safety,
    build_expected_return_calibration,
    expected_return_safety_reason,
    infer_expected_return_source,
    write_expected_return_calibration,
)
from stockml.diagnostics.validation_bucket_calibration import CALIBRATION_COLUMNS
from stockml.diagnostics.validation_bucket_calibration import build_validation_bucket_calibration
import stockml.trading.trade_quality_gate as trade_quality_gate
from stockml.trading.config import AlpacaConfig
from stockml.trading.trade_quality_gate import apply_trade_quality_gate


def _cfg(**overrides):
    values = dict(
        api_key="",
        secret_key="",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=False,
        max_orders=10,
        max_notional_per_order=1000,
        max_total_notional=10000,
        min_trade_price=5,
        max_sector_fraction=0.4,
        min_side_probability=0.55,
        min_abs_probability_edge=0.05,
        min_intraday_volume=100000,
        min_market_cap=300000000,
        min_risk_adjusted_score=0.005,
        min_expected_trade_return=0.002,
        transaction_cost_bps=10,
        account_equity=100000,
        max_position_pct=0.05,
        allow_short_selling=True,
        extended_hours=False,
        live_trading_enabled=False,
        paper_trading_enabled=True,
    )
    values.update(overrides)
    return AlpacaConfig(**values)


def _candidate(symbol="AAA", expected=0.02, risk=0.02, model=0.5):
    return {
        "symbol": symbol,
        "ticker": symbol,
        "side": "buy",
        "trade_action": "Long",
        "candidate_rank": 1,
        "model_score": model,
        "expected_trade_return": expected,
        "risk_adjusted_score": risk,
        "side_probability": 0.7,
        "probability_edge": 0.2,
        "current_price": 20,
        "open_price": 20,
        "intraday_high": 21,
        "intraday_low": 19,
        "intraday_volume": 1_000_000,
        "avg_dollar_volume_20d": 80_000_000,
        "market_cap": 10_000_000_000,
        "volatility_20d": 0.02,
    }


def test_unrealistic_expected_return_is_flagged():
    report = build_expected_return_calibration(pd.DataFrame([_candidate("ICCM", expected=1961.05, risk=980.5257)]))
    row = report.iloc[0]
    assert row["expected_return_quality"] == "invalid"
    assert row["execution_block_reason"] == "expected_return_uncalibrated"
    assert row["execution_allowed"] is False or row["execution_allowed"] == False


def test_bps_vs_percent_ambiguity_is_flagged():
    source, quality, issue = infer_expected_return_source(pd.Series(_candidate(expected=0.08, risk=0.01)))
    assert source == "percent_or_ratio"
    assert quality == "uncalibrated"
    assert issue == "requires_bucket_validation"


def test_raw_model_score_mislabelled_as_expected_return_is_flagged():
    report = build_expected_return_calibration(pd.DataFrame([_candidate("RAW", expected=0.72, risk=0.72, model=0.72)]))
    assert report.iloc[0]["expected_return_source"] == "raw_model_score"
    assert report.iloc[0]["expected_return_quality"] == "invalid"


def test_validated_bucket_return_is_used_as_calibrated_expected_return():
    candidates = pd.DataFrame([_candidate("BUCKET", expected=1.5, risk=0.75, model=0.75)])
    validation = pd.DataFrame([{"rank_bucket": "D10", "validated_expected_return_bps": 42, "validated_hit_rate": 0.58, "validated_avg_gain": 120, "validated_avg_loss": -70}])
    report = build_expected_return_calibration(candidates, validation)
    row = report.iloc[0]
    assert row["expected_return_source"] == "historical_bucket_return"
    assert row["expected_return_quality"] == "calibrated"
    assert row["validated_expected_return_bps"] == 42
    assert row["execution_allowed"] is True or row["execution_allowed"] == True


def test_uncalibrated_expected_return_blocks_execution():
    frame = pd.DataFrame([{**_candidate("BLOCK", expected=1961.05, risk=980.5257), "trade_quality_status": "approved", "order_eligible": True, "trade_quality_reason": "approved"}])
    safe = apply_expected_return_execution_safety(frame)
    assert safe.iloc[0]["trade_quality_status"] == "rejected"
    assert safe.iloc[0]["order_eligible"] is False or safe.iloc[0]["order_eligible"] == False
    assert "expected_return_uncalibrated" in safe.iloc[0]["trade_quality_reason"]


def test_empty_validation_bucket_calibration_blocks_raw_expected_return_fallback():
    candidates = pd.DataFrame([_candidate("RAW_OK", expected=0.01, risk=0.01, model=0.75)])
    empty_bucket_calibration = pd.DataFrame(columns=CALIBRATION_COLUMNS)

    report = build_expected_return_calibration(candidates, empty_bucket_calibration)

    assert report.iloc[0]["expected_return_quality"] == "invalid"
    assert report.iloc[0]["execution_allowed"] is False or report.iloc[0]["execution_allowed"] == False
    assert report.iloc[0]["execution_block_reason"] == "expected_return_uncalibrated"


def test_safety_reason_does_not_fallback_to_raw_expected_return():
    row = pd.Series(_candidate("RAW_OK", expected=0.01, risk=0.01, model=0.75))

    assert expected_return_safety_reason(row) == "expected_return_uncalibrated"


def test_trade_quality_gate_rejects_uncalibrated_expected_return_without_changing_score(monkeypatch):
    monkeypatch.setattr(trade_quality_gate, "latest_expected_return_calibration", lambda: pd.DataFrame())
    signal = pd.DataFrame([_candidate("GATE", expected=1961.05, risk=980.5257)])
    out = apply_trade_quality_gate(signal, _cfg())
    row = out.iloc[0]
    assert row["risk_adjusted_score"] == 980.5257
    assert row["trade_quality_status"] == "rejected"
    assert "expected_return_uncalibrated" in row["trade_quality_reason"]


def test_trade_quality_gate_uses_latest_validation_bucket_calibration(monkeypatch):
    validation = pd.DataFrame(
        {
            "ticker": [f"T{i:04d}" for i in range(1000)],
            "side": ["Long"] * 1000,
            "model_score": [1.0 - i / 1000 for i in range(1000)],
            "rank_overall": list(range(1, 1001)),
            "forward_5d_return": [0.02] * 1000,
            "split": ["validation"] * 1000,
        }
    )
    calibration, _ = build_validation_bucket_calibration(validation)
    monkeypatch.setattr(trade_quality_gate, "latest_expected_return_calibration", lambda: calibration)
    signal = pd.DataFrame(
        [
            {
                **_candidate("SAFE", expected=1961.05, risk=0.50, model=0.99),
                "candidate_rank": 1,
                "current_price": 20,
                "open_price": 20,
                "intraday_high": 21,
                "intraday_low": 19,
                "intraday_volume": 2_000_000,
                "market_cap": 10_000_000_000,
                "avg_dollar_volume_20d": 80_000_000,
                "volatility_20d": 0.02,
            }
        ]
    )

    out = apply_trade_quality_gate(signal, _cfg())

    assert out.iloc[0]["expected_return_quality"] == "usable"
    assert "expected_return_uncalibrated" not in out.iloc[0]["trade_quality_reason"]


def test_write_outputs(tmp_path: Path):
    outputs = write_expected_return_calibration(pd.DataFrame([_candidate("AAA", expected=1961.05)]), output_dir=tmp_path, stamp="20260701_120000")
    assert outputs.rows == 1
    assert outputs.unrealistic_rows == 1
    assert outputs.diagnostic_path.exists()
    assert outputs.summary_path.exists()
