from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, select

from portal.app import create_app
from stockml.db.schema import closed_trades_attribution, create_all, metadata
from stockml.reports.closed_trade_metrics import classify_close_reason, mfe_mae_metrics
from stockml.reports.closed_trades_attribution import (
    attribution_summary,
    build_attribution_row,
    build_closed_trades_attribution,
    persist_attribution,
)


NOW = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)


def test_closed_trades_table_registered_and_migration_exists():
    assert "closed_trades_attribution" in metadata.tables
    assert closed_trades_attribution.primary_key.columns.keys() == ["position_id"]
    up = Path("migrations/022_closed_trades_attribution_up.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS closed_trades_attribution" in up
    assert "position_id BIGINT PRIMARY KEY" in up
    assert "max_favourable_bps" in up

    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    assert "closed_trades_attribution" in inspect(engine).get_table_names()


def test_stop_loss_trade_was_never_positive():
    bars = pd.DataFrame(
        [
            {"timestamp": "2026-06-04T14:35:00Z", "high": 99.5, "low": 97.0, "close": 98.0},
            {"timestamp": "2026-06-04T14:40:00Z", "high": 98.5, "low": 96.0, "close": 96.5},
        ]
    )
    row = build_attribution_row(
        {
            "position_id": 1,
            "symbol": "AAA",
            "direction": "long",
            "opened_at": "2026-06-04T14:30:00Z",
            "closed_at": "2026-06-04T14:45:00Z",
            "signal_price": 100,
            "entry_fill": 100,
            "exit_fill": 97,
            "quantity": 10,
            "close_reason": "hard_stop_loss",
        },
        bars=bars,
        created_at=NOW,
    )

    assert row["close_reason"] == "STOP_LOSS"
    assert row["max_favourable_bps"] == 0.0
    assert row["max_adverse_bps"] == -400.0
    assert row["realized_net_bps"] < 0


def test_gave_back_winner_before_close():
    bars = pd.DataFrame(
        [
            {"timestamp": "2026-06-04T14:35:00Z", "high": 105.0, "low": 99.5, "close": 104.0},
            {"timestamp": "2026-06-04T14:40:00Z", "high": 102.0, "low": 98.0, "close": 98.5},
        ]
    )
    row = build_attribution_row(
        {
            "position_id": 2,
            "symbol": "BBB",
            "direction": "long",
            "opened_at": "2026-06-04T14:30:00Z",
            "closed_at": "2026-06-04T14:45:00Z",
            "signal_price": 100,
            "entry_fill": 100,
            "exit_fill": 99,
            "quantity": 10,
            "close_reason": "manual_close",
        },
        bars=bars,
        created_at=NOW,
    )

    assert row["max_favourable_bps"] == 500.0
    assert row["realized_net_bps"] < 0
    summary = attribution_summary(pd.DataFrame([row]))
    assert summary["negative_but_mfe_positive_count"] == 1


def test_one_bar_mfe_mae_equals_open_to_close_move():
    bars = pd.DataFrame([{"timestamp": "2026-06-04T14:35:00Z", "high": 105.0, "low": 95.0, "close": 101.0}])
    metrics = mfe_mae_metrics(bars, entry_fill=100, direction="long", opened_at="2026-06-04T14:30:00Z")
    assert metrics["max_favourable_bps"] == 100.0
    assert metrics["max_adverse_bps"] == 100.0


def test_short_direction_reverses_price_math():
    row = build_attribution_row(
        {
            "position_id": 3,
            "symbol": "CCC",
            "direction": "short",
            "opened_at": "2026-06-04T14:30:00Z",
            "closed_at": "2026-06-04T15:00:00Z",
            "signal_price": 50,
            "entry_fill": 49,
            "exit_target": 45,
            "exit_fill": 45,
            "quantity": 20,
            "close_reason": "take_profit",
        },
        bars=pd.DataFrame([{"timestamp": "2026-06-04T14:35:00Z", "high": 49.5, "low": 44.5, "close": 45.0}]),
        created_at=NOW,
    )
    assert row["direction"] == "short"
    assert row["signal_to_entry_bps"] < 0
    assert row["entry_to_exit_bps"] > 0
    assert row["close_reason"] == "TAKE_PROFIT"


def test_classifies_expected_close_reasons():
    assert classify_close_reason("time_stop") == "TIME_STOP"
    assert classify_close_reason("signal_flip") == "SIGNAL_FLIP"
    assert classify_close_reason("rotation") == "ROTATION_OUT"
    assert classify_close_reason("eod flatten") == "EOD_FLATTEN"
    assert classify_close_reason("operator_close") == "MANUAL"
    assert classify_close_reason("mystery") == "OTHER"


def test_persist_attribution_is_idempotent():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    frame = build_closed_trades_attribution(
        [
            {
                "position_id": 10,
                "symbol": "DDD",
                "direction": "long",
                "opened_at": "2026-06-04T14:30:00Z",
                "closed_at": "2026-06-04T15:00:00Z",
                "signal_price": 10,
                "entry_fill": 10,
                "exit_fill": 11,
                "quantity": 5,
                "close_reason": "take_profit",
            }
        ],
        created_at=NOW,
    )

    assert persist_attribution(frame, engine=engine) == 1
    assert persist_attribution(frame, engine=engine) == 1
    with engine.connect() as conn:
        rows = conn.execute(select(closed_trades_attribution)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "DDD"


def test_portal_closed_trades_page_and_csv_render(tmp_path: Path):
    trading = tmp_path / "data" / "trading"
    trading.mkdir(parents=True)
    frame = build_closed_trades_attribution(
        [
            {
                "position_id": 20,
                "symbol": "EEE",
                "direction": "long",
                "opened_at": "2026-06-04T14:30:00Z",
                "closed_at": "2026-06-04T15:00:00Z",
                "signal_price": 10,
                "entry_fill": 10,
                "exit_fill": 9,
                "quantity": 5,
                "close_reason": "stop_loss",
            }
        ],
        created_at=NOW,
    )
    frame.to_csv(trading / "closed_trades_attribution_20260604_150000.csv", index=False)

    client = create_app(tmp_path).test_client()
    page = client.get("/reports/closed_trades")
    assert page.status_code == 200
    assert b"Closed Trades Attribution" in page.data
    assert b"EEE" in page.data

    csv_response = client.get("/reports/closed_trades.csv")
    assert csv_response.status_code == 200
    assert csv_response.mimetype == "text/csv"
    assert b"position_id,symbol" in csv_response.data
