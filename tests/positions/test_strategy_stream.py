from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from stockml.trading.order_planner import build_candidate_pool, build_order_plan
from stockml.trading.snapshot_schema import StrategyStream
from stockml.trading.snapshot_writer import build_snapshot_row, write_snapshot_csv
from stockml.trading.config import AlpacaConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def config(**overrides):
    values = {
        "api_key": "",
        "secret_key": "",
        "base_url": "https://paper-api.alpaca.markets",
        "submit_orders": False,
        "extended_hours": False,
        "max_orders": 2,
        "max_notional_per_order": 500.0,
        "max_total_notional": 1000.0,
        "min_trade_price": 5.0,
        "max_sector_fraction": 1.0,
        "min_side_probability": 0.55,
        "min_abs_probability_edge": 0.05,
        "min_intraday_volume": 100000,
        "min_market_cap": 300000000.0,
        "min_risk_adjusted_score": 0.001,
        "transaction_cost_bps": 10.0,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


def trade_signal(ticker: str, action: str, score: float) -> dict[str, object]:
    return {
        "ticker": ticker,
        "date": "2026-05-08",
        "trade_action": action,
        "side_probability": 0.8,
        "probability_edge": 0.2 if action == "Long" else -0.2,
        "expected_trade_return": 0.02,
        "close": 20,
        "open": 20,
        "high": 21,
        "low": 19,
        "volume": 1_000_000,
        "avg_dollar_volume_20d": 60_000_000,
        "market_cap": 20_000_000_000,
        "volatility_20d": 0.02,
        "risk_adjusted_score": score,
        "sector": "Technology",
    }


def test_migration_defaults_existing_positions_and_candidates_to_multi_day():
    sql = (PROJECT_ROOT / "migrations" / "016_strategy_stream_positions_up.sql").read_text(encoding="utf-8")

    assert "ALTER TABLE positions" in sql
    assert "ALTER TABLE candidate_pool" in sql
    assert "DEFAULT 'multi_day_forecast'" in sql
    assert "SET strategy_stream = 'multi_day_forecast'" in sql
    assert "must_flatten_at_eod BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "max_hold_until DATE" in sql


def test_order_planner_emits_multi_day_strategy_stream_by_default():
    signals = pd.DataFrame([trade_signal("AAA", "Long", 0.5)])

    pool = build_candidate_pool(signals, config(max_orders=1))
    plan = build_order_plan(signals, config(max_orders=1))

    assert set(pool["strategy_stream"]) == {"multi_day_forecast"}
    assert set(plan["strategy_stream"]) == {"multi_day_forecast"}
    assert pool["must_flatten_at_eod"].eq(False).all()
    assert plan["must_flatten_at_eod"].eq(False).all()


def test_snapshot_writers_emit_strategy_stream_on_every_row():
    text = write_snapshot_csv(
        [
            ("model_shortlist", [{"symbol": "AAA", "side": "buy"}], "", "fixture"),
            ("action_queue", [{"symbol": "BBB", "side": "long", "strategy_stream": "same_day_momentum"}], "", "fixture"),
            ("open_positions", [{"symbol": "CCC", "side": "long", "trading_stream": "multi_day"}], "", "fixture"),
            ("intraday_promotion", [{"symbol": "DDD", "side": "long", "verdict": "watch"}], "", "fixture"),
        ]
    )

    rows = list(pd.read_csv(io.StringIO(text)).to_dict("records"))
    by_symbol = {row["symbol"]: row["strategy_stream"] for row in rows}
    assert by_symbol == {
        "AAA": StrategyStream.MULTI_DAY_FORECAST.value,
        "BBB": StrategyStream.SAME_DAY_MOMENTUM.value,
        "CCC": StrategyStream.MULTI_DAY_FORECAST.value,
        "DDD": StrategyStream.SAME_DAY_MOMENTUM.value,
    }


def test_snapshot_rejects_unknown_strategy_stream():
    try:
        build_snapshot_row("open_positions", {"symbol": "BAD", "strategy_stream": "mystery"}, snapshot_at=pd.Timestamp.utcnow().to_pydatetime())
    except ValueError as exc:
        assert str(exc) == "invalid_snapshot_strategy_stream"
    else:
        raise AssertionError("expected invalid strategy stream")
