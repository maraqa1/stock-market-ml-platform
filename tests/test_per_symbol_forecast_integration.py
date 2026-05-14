from pathlib import Path

import pandas as pd

from portal.app import create_app
from portal.services.data_estate import DATASETS


def test_data_estate_registers_per_symbol_forecast_dataset():
    assert any(spec["key"] == "per_symbol_forecast" and spec["file_key"] == "per_symbol_forecast" for spec in DATASETS)


def test_trading_page_renders_per_symbol_forecast_panel(tmp_path: Path):
    path = tmp_path / "data" / "trading" / "per_symbol_forecast" / "per_symbol_forecast_20260514_120000.csv"
    path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "diagnostic_only": True,
                "symbol": "AAPL",
                "side": "buy",
                "current_trade_action": "Long",
                "candidate_rank": 1,
                "expected_move_bps": 100,
                "volatility_adjusted_score": 2.5,
                "regime_label": "normal",
                "forecast_reason": "RANK_TOP_DECILE_LOW_VOL",
                "suggested_stop_bps": 200,
                "suggested_take_profit_bps": 300,
                "tier_c_status": "uncalibrated",
                "forecast_confirmation": "confirmed",
                "confirmation_score": 100,
            }
        ]
    ).to_csv(path, index=False)
    app = create_app(tmp_path)
    app.config.update(TESTING=True)

    response = app.test_client().get("/trading")

    assert response.status_code == 200
    assert b"Per-Symbol Forecasts" in response.data
    assert b"Diagnostic only" in response.data
    assert b"Confirmed" in response.data
    assert b"AAPL" in response.data


def test_trading_snapshot_includes_per_symbol_forecast_pool(tmp_path: Path):
    path = tmp_path / "data" / "trading" / "per_symbol_forecast" / "per_symbol_forecast_20260514_120000.csv"
    path.parent.mkdir(parents=True)
    pd.DataFrame([{"diagnostic_only": True, "symbol": "AAPL", "side": "buy", "candidate_rank": 1}]).to_csv(path, index=False)
    app = create_app(tmp_path)
    app.config.update(TESTING=True)

    response = app.test_client().get("/trading/snapshot.csv")

    assert response.status_code == 200
    assert b"per_symbol_forecast" in response.data
    assert b"AAPL" in response.data
