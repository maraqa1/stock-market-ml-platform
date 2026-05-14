from pathlib import Path

import pandas as pd

from stockml.trading.per_symbol_forecast.generate import forecast_rows, generate_per_symbol_forecast
from stockml.trading.per_symbol_forecast.schema import OUTPUT_COLUMNS


def _candidate(symbol: str = "AAPL", **overrides):
    row = {
        "symbol": symbol,
        "side": "buy",
        "trade_action": "Long",
        "candidate_rank": 1,
        "risk_adjusted_score": 0.05,
        "expected_trade_return": 0.01,
        "current_price": 100,
        "volatility_20d": 0.02,
        "liquidity_tier": "high",
        "volatility_tier": "low",
    }
    row.update(overrides)
    return row


def test_forecast_rows_schema_is_stable():
    frame = forecast_rows(pd.DataFrame([_candidate()]), generated_at="2026-05-14T12:00:00+00:00")

    assert list(frame.columns) == OUTPUT_COLUMNS
    assert bool(frame.iloc[0]["diagnostic_only"]) is True
    assert frame.iloc[0]["tier_c_status"] == "uncalibrated"


def test_generate_per_symbol_forecast_writes_append_only_artifact(tmp_path: Path):
    source = tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260514_120000.csv"
    source.parent.mkdir(parents=True)
    pd.DataFrame([_candidate("AAPL"), _candidate("MSFT", candidate_rank=2)]).to_csv(source, index=False)

    result = generate_per_symbol_forecast(tmp_path, stamp="20260514_120001")

    output = Path(result["path"])
    written = pd.read_csv(output)
    assert result["rows"] == 2
    assert output.name == "per_symbol_forecast_20260514_120001.csv"
    assert list(written.columns) == OUTPUT_COLUMNS
    assert written["symbol"].tolist() == ["AAPL", "MSFT"]
