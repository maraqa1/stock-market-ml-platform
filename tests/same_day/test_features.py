from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError

from stockml.db.schema import create_all, intraday_features
from stockml.intraday.features import Bar, Quote
from stockml.intraday.provider import MarketCalendar
from stockml.same_day.feature_worker import FeatureRow, feature_tick, write_feature_row
from stockml.same_day.features import ConfigurationError, compute_features, validate_cadence
from stockml.same_day.universe import build_same_day_universe


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def _bars(decision: datetime = NOW) -> list[Bar]:
    start = decision - timedelta(minutes=95)
    rows = []
    price = 100.0
    for ts in pd.date_range(start, periods=20, freq="5min", tz="UTC"):
        rows.append(Bar(timestamp=ts.to_pydatetime(), open=price, high=price + 1, low=price - 1, close=price + 0.4, volume=10_000, vwap=price + 0.2))
        price += 0.5
    return rows


def _quote() -> Quote:
    return Quote(symbol="AAA", bid=109.9, ask=110.0, last_price=110, quote_ts=NOW - timedelta(minutes=5), fetched_at=NOW)


def _context() -> dict[str, object]:
    return {
        "open_at": datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc),
        "close_at": datetime(2026, 5, 11, 20, 0, tzinfo=timezone.utc),
        "spy_intraday_move_pct": 0.5,
        "sector_etf_intraday_move_pct": 0.2,
    }


def test_intraday_range_position_bounded():
    features = compute_features("AAA", NOW, _quote(), _bars(), _context(), {"avg_dollar_volume_20d": 50_000_000, "prior_day_close": 99})

    assert features is not None
    assert 0 <= features["intraday_range_position"] <= 1

    flat = pd.DataFrame(
        [{"timestamp": NOW - timedelta(minutes=5 * i), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1000} for i in range(20, 0, -1)]
    )
    flat_features = compute_features("AAA", NOW, _quote(), flat, _context(), {})
    assert flat_features is not None
    assert flat_features["intraday_range_position"] == 0.5


def test_features_handle_missing_bars():
    assert compute_features("AAA", NOW, _quote(), [], _context(), {}) is None

    one_bar = [Bar(timestamp=NOW - timedelta(minutes=5), open=10, high=11, low=9, close=10.5, volume=1000)]
    features = compute_features("AAA", NOW, _quote(), one_bar, _context(), {})
    assert features is None


def test_universe_membership():
    validated = pd.DataFrame(
        [
            {"symbol": "GOOD", "close": 20, "avg_dollar_volume_20d": 30_000_000, "market_cap": 800_000_000},
            {"symbol": "THIN", "close": 20, "avg_dollar_volume_20d": 5_000_000, "market_cap": 800_000_000},
            {"symbol": "CHEAP", "close": 2, "avg_dollar_volume_20d": 30_000_000, "market_cap": 800_000_000},
            {"symbol": "SMALL", "close": 20, "avg_dollar_volume_20d": 30_000_000, "market_cap": 100_000_000},
            {"symbol": "HALT", "close": 20, "avg_dollar_volume_20d": 30_000_000, "market_cap": 800_000_000, "is_halted": True},
        ]
    )

    assert build_same_day_universe(date(2026, 5, 11), validated=validated, metadata=pd.DataFrame(), halted_symbols={"NONE"}) == ["GOOD"]


def test_uniqueness_constraint():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    row = {
        "computed_at": NOW,
        "decision_time": NOW,
        "bar_close_at": NOW - timedelta(minutes=5),
        "symbol": "AAA",
        "status": "ok",
        "features": {"return_5m_pct": 0.01},
    }

    with engine.begin() as conn:
        conn.execute(insert(intraday_features).values(**row))
        with pytest.raises(IntegrityError):
            conn.execute(insert(intraday_features).values(**row))


def test_cadence_floor():
    validate_cadence(5)
    with pytest.raises(ConfigurationError):
        validate_cadence(4)


class FakeProvider:
    def __init__(self):
        self.calendar = MarketCalendar(NOW - timedelta(hours=2), NOW + timedelta(hours=5), True)

    def fetch_market_calendar(self, selected: date):
        return self.calendar

    def fetch_quote(self, symbol: str):
        return _quote()

    def fetch_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 24):
        return _bars()


def allow_gate(**kwargs):
    return type("Verdict", (), {"allow": True, "tripped": []})()


def test_feature_worker_writes_one_row_per_symbol():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)

    result = feature_tick(
        now=NOW,
        selected_date=NOW.date(),
        provider=FakeProvider(),
        engine=engine,
        universe_loader=lambda selected: ["AAA", "BBB"],
        market_context_loader=lambda stamp: _context(),
        symbol_context_loader=lambda symbol: {"avg_dollar_volume_20d": 50_000_000},
        kill_switch_gate=allow_gate,
    )

    assert result["status"] == "ok"
    assert result["features_written"] == 2


def test_write_feature_row_handles_duplicate_as_false():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    row = FeatureRow(NOW, NOW, NOW - timedelta(minutes=5), "AAA", "ok", {"x": 1})

    assert write_feature_row(row, engine=engine) is True
    assert write_feature_row(row, engine=engine) is False
