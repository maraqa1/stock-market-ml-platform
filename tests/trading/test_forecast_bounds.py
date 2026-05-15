from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, forecast_cap_log
from stockml.trading.per_symbol_forecast.generate import log_forecast_caps


def test_cap_logging_records_capped_forecast_rows():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    frame = pd.DataFrame(
        [
            {
                "symbol": "FWRD",
                "cap_applied": True,
                "pre_cap_expected_5d_bps": 10626.0,
                "expected_5d_return_bps": 500.0,
            }
        ]
    )

    logged = log_forecast_caps(
        frame,
        engine=engine,
        forecast_run_id="per_symbol_forecast_20260515_054051",
        now=datetime(2026, 5, 15, 5, 40, tzinfo=timezone.utc),
    )

    assert logged == 1
    with engine.connect() as conn:
        row = conn.execute(select(forecast_cap_log)).mappings().one()
    assert row["symbol"] == "FWRD"
    assert row["field_name"] == "expected_5d_return_bps"
    assert row["pre_cap_value"] == 10626.0
    assert row["cap_applied"] == 500.0

