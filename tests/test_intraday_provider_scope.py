from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, insert

from stockml.db.schema import create_all, pipeline_runs, shortlist_snapshots
from stockml.intraday.config import load_intraday_config
from stockml.intraday.provider import IndependentReferenceProvider, IntradayProvider
from stockml.intraday.scope import scope_for_today
from stockml.trading.config import AlpacaConfig


def cfg() -> AlpacaConfig:
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=False,
        extended_hours=False,
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
        transaction_cost_bps=10,
        paper_trading_enabled=True,
        live_trading_enabled=False,
    )


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        raise AssertionError("should not raise")


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if "quotes" in url:
            return FakeResponse({"quote": {"bp": 10.0, "ap": 10.1, "bs": 100, "as": 120, "t": "2026-05-11T14:30:00Z"}})
        if "bars" in url:
            return FakeResponse({"bars": [{"o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1000, "vw": 10.4, "t": "2026-05-11T14:30:00Z"}]})
        if "calendar" in url:
            return FakeResponse([{"open": "2026-05-11T13:30:00Z", "close": "2026-05-11T20:00:00Z"}])
        return FakeResponse({})


def test_intraday_config_enforces_five_minute_floor(tmp_path: Path):
    path = tmp_path / "intraday.yaml"
    path.write_text("version: 1\ncadence_minutes: 4\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_intraday_config(path)


def test_intraday_config_loads_default_file():
    config = load_intraday_config()

    assert config.cadence_minutes == 5
    assert config.shadow_only is True
    assert config.reference_provider_enabled is False


def test_provider_fetches_quote_bars_and_calendar_read_only():
    session = FakeSession()
    calls = []
    provider = IntradayProvider(cfg(), session=session, logger=lambda event, payload: calls.append((event, payload)))

    quote = provider.fetch_quote("tsla")
    bars = provider.fetch_bars("tsla")
    calendar = provider.fetch_market_calendar(date(2026, 5, 11))

    assert quote.symbol == "TSLA"
    assert quote.bid == 10.0
    assert len(bars) == 1
    assert calendar.is_open is True
    assert all(call["url"].startswith(("https://data.alpaca.markets", "https://paper-api.alpaca.markets")) for call in session.calls)
    assert not any("/v2/orders" in call["url"] or "/v2/positions" in call["url"] for call in session.calls)
    assert {payload["endpoint"] for _, payload in calls} == {"latest_quote", "bars", "calendar"}


def test_provider_calendar_parses_market_local_times_to_utc():
    class CalendarSession(FakeSession):
        def get(self, url, headers=None, params=None, timeout=None):
            self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
            return FakeResponse([{"open": "09:30", "close": "16:00"}])

    provider = IntradayProvider(cfg(), session=CalendarSession(), logger=lambda event, payload: None)

    calendar = provider.fetch_market_calendar(date(2026, 5, 12))

    assert calendar.is_open is True
    assert calendar.open_at == datetime(2026, 5, 12, 13, 30, tzinfo=timezone.utc)
    assert calendar.close_at == datetime(2026, 5, 12, 20, 0, tzinfo=timezone.utc)


def test_provider_parses_nanosecond_quote_timestamps():
    class NanoQuoteSession(FakeSession):
        def get(self, url, headers=None, params=None, timeout=None):
            self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
            return FakeResponse({"quote": {"bp": 10.0, "ap": 10.1, "t": "2026-06-30T20:00:00.173316186Z"}})

    provider = IntradayProvider(cfg(), session=NanoQuoteSession(), logger=lambda event, payload: None)

    quote = provider.fetch_quote("BNY")

    assert quote.quote_ts == datetime(2026, 6, 30, 20, 0, 0, 173316, tzinfo=timezone.utc)


def test_independent_reference_provider_is_disabled_hook():
    provider = IndependentReferenceProvider()

    assert provider.fetch_quote("TSLA") is None
    assert provider.fetch_bars("TSLA") is None


def test_scope_for_today_uses_db_shortlist_and_injected_positions_loader():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(pipeline_runs).values(run_id="2026-05-11-A", started_at=datetime(2026, 5, 11, tzinfo=timezone.utc), status="success"))
        conn.execute(
            insert(shortlist_snapshots),
            [
                {"run_id": "2026-05-11-A", "rank": 1, "symbol": "TSLA", "bias": "long", "score": 0.8, "in_basket": True},
                {"run_id": "2026-05-11-A", "rank": 2, "symbol": "NVDA", "bias": "short", "score": 0.7, "in_basket": False},
            ],
        )

    symbols = scope_for_today(date(2026, 5, 11), positions_loader=lambda: [{"symbol": "AAPL"}], engine=engine)

    assert symbols == ["AAPL", "NVDA", "TSLA"]


def test_scope_for_today_falls_back_to_candidate_artifact(tmp_path: Path):
    path = tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260511_120000.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"symbol": "MSFT"}, {"symbol": "META"}]).to_csv(path, index=False)

    symbols = scope_for_today(date(2026, 5, 11), root=tmp_path, positions_loader=lambda: [{"symbol": "AAPL"}])

    assert symbols == ["AAPL", "META", "MSFT"]
