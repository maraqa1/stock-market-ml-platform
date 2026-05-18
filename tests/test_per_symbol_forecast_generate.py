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
    assert frame.iloc[0]["forecast_confirmation"] in {"confirmed", "weak_confirm", "conflicted", "insufficient_data"}
    assert frame.iloc[0]["expected_move_bps_calibrated"] <= frame.iloc[0]["expected_move_bps"]
    assert "expected_5d_return_bps" in frame.columns
    assert "expected_5d_return" not in frame.columns
    assert frame.iloc[0]["liquidity_tier"] == "high"
    assert frame.iloc[0]["volatility_tier"] == "low"
    assert bool(frame.iloc[0]["liquidity_ok"]) is True
    assert bool(frame.iloc[0]["volatility_ok"]) is True


def test_forecast_rows_preserve_short_quality_tiers_for_confirmation():
    frame = forecast_rows(
        pd.DataFrame(
            [
                _candidate(
                    "SHORTY",
                    side="sell",
                    trade_action="Short",
                    liquidity_tier="medium",
                    volatility_tier="medium",
                )
            ]
        ),
        generated_at="2026-05-14T12:00:00+00:00",
    )

    row = frame.iloc[0]
    assert row["current_trade_action"] == "Short"
    assert row["side_alignment"] == "aligned"
    assert row["liquidity_tier"] == "medium"
    assert row["volatility_tier"] == "medium"
    assert bool(row["liquidity_ok"]) is True
    assert bool(row["volatility_ok"]) is True


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


def test_forecast_rows_include_open_positions_beyond_candidate_limit():
    candidates = pd.DataFrame([_candidate("AAPL"), _candidate("MSFT", candidate_rank=2)])
    positions = pd.DataFrame(
        [
            {
                "symbol": "HELD",
                "side": "long",
                "qty": 4,
                "avg_entry_price": 20,
                "current_price": 21,
                "unrealized_plpc": 0.05,
            }
        ]
    )

    frame = forecast_rows(candidates, positions=positions, generated_at="2026-05-14T12:00:00+00:00", limit=1)

    assert frame["symbol"].tolist() == ["AAPL", "HELD"]
    held = frame[frame["symbol"] == "HELD"].iloc[0]
    assert held["forecast_scope"] == "open_position"
    assert bool(held["is_open_position"]) is True
    assert held["position_qty"] == 4
    assert held["position_entry_price"] == 20
    assert held["position_unrealized_plpc"] == 0.05


def test_forecast_rows_mark_candidate_and_open_position_overlap():
    candidates = pd.DataFrame([_candidate("AAPL")])
    positions = pd.DataFrame([{"symbol": "AAPL", "side": "long", "qty": 2, "avg_entry_price": 90, "current_price": 100, "unrealized_plpc": 0.1}])

    frame = forecast_rows(candidates, positions=positions, generated_at="2026-05-14T12:00:00+00:00")

    row = frame.iloc[0]
    assert row["forecast_scope"] == "candidate_and_open_position"
    assert bool(row["is_open_position"]) is True
    assert row["position_qty"] == 2
