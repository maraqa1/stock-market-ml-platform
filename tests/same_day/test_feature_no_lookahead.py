from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from stockml.intraday.features import Bar, Quote
from stockml.same_day.feature_worker import in_active_hours
from stockml.same_day.features import compute_features


DECISION = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def _bars(decision_bar_close: float) -> pd.DataFrame:
    rows = []
    price = 100.0
    for ts in pd.date_range(DECISION - timedelta(minutes=90), DECISION, freq="5min", tz="UTC"):
        close = price + 0.2
        if ts.to_pydatetime() == DECISION:
            close = decision_bar_close
        rows.append(
            {
                "timestamp": ts,
                "open": price,
                "high": max(price + 1, close),
                "low": min(price - 1, close),
                "close": close,
                "volume": 10000,
                "vwap": price + 0.1,
            }
        )
        price += 0.5
    return pd.DataFrame(rows)


def _quote() -> Quote:
    return Quote(symbol="AAA", bid=100, ask=100.1, last_price=100.05, quote_ts=DECISION - timedelta(minutes=5), fetched_at=DECISION)


def test_feature_lag_no_lookahead():
    context = {
        "open_at": datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc),
        "close_at": datetime(2026, 5, 11, 20, 0, tzinfo=timezone.utc),
        "spy_intraday_move_pct": 0.1,
        "sector_etf_intraday_move_pct": 0.1,
    }

    first = compute_features("AAA", DECISION, _quote(), _bars(10), context, {"avg_dollar_volume_20d": 30_000_000})
    second = compute_features("AAA", DECISION, _quote(), _bars(1000), context, {"avg_dollar_volume_20d": 30_000_000})

    assert first == second


def test_market_hours_filter():
    assert not in_active_hours(datetime(2026, 5, 11, 13, 55, tzinfo=timezone.utc))
    assert in_active_hours(datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc))
    assert in_active_hours(datetime(2026, 5, 11, 19, 0, tzinfo=timezone.utc))
    assert not in_active_hours(datetime(2026, 5, 11, 19, 5, tzinfo=timezone.utc))


def test_no_submit_order_calls_in_same_day_source():
    source_root = __import__("pathlib").Path(__file__).resolve().parents[2] / "src" / "stockml" / "same_day"
    text = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))

    assert "submit_order" not in text
    assert "/v2/orders" not in text
