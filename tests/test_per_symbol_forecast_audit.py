from pathlib import Path

import pandas as pd

from stockml.trading.per_symbol_forecast.audit import audit_per_symbol_forecast


def test_weekly_audit_writes_baseline_report(tmp_path: Path):
    forecast = tmp_path / "data" / "trading" / "per_symbol_forecast" / "per_symbol_forecast_20260514_120000.csv"
    forecast.parent.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAPL", "expected_5d_return_bps": 100, "diagnostic_only": True}]).to_csv(forecast, index=False)

    result = audit_per_symbol_forecast(tmp_path, stamp="20260514")
    report = pd.read_csv(result["path"])

    assert Path(result["path"]).name == "per_symbol_forecast_audit_20260514.csv"
    assert report.iloc[0]["forecast_rows"] == 1
    assert "tier_b_expected_5d_return_bps_correlation" in report.columns
    assert report.iloc[0]["audit_status"] == "awaiting_realized_outcomes"
