from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, intraday_decisions
from stockml.intraday.features import Bar, NightlySignal, Quote
from stockml.intraday.provider import MarketCalendar
from stockml.intraday.worker import intraday_tick, market_is_open


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)
PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class FakeProvider:
    def __init__(self, calendar=None, fail_symbols=None):
        self.calendar = calendar or MarketCalendar(NOW - timedelta(hours=1), NOW + timedelta(hours=5), True)
        self.fail_symbols = set(fail_symbols or [])
        self.quotes = []
        self.bars = []

    def fetch_market_calendar(self, selected: date):
        return self.calendar

    def fetch_quote(self, symbol: str):
        self.quotes.append(symbol)
        if symbol in self.fail_symbols:
            raise RuntimeError("quote_failed")
        return Quote(symbol, bid=100, ask=100.1, bid_size=120, ask_size=100, last_price=100.05, quote_ts=NOW, fetched_at=NOW)

    def fetch_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 12):
        self.bars.append((symbol, timeframe, limit))
        return [Bar(open=100 + i * 0.1, high=101 + i * 0.1, low=99 + i * 0.1, close=100 + i * 0.1, volume=1000, vwap=100) for i in range(limit)]


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def allow_gate(**kwargs):
    return type("Verdict", (), {"allow": True, "tripped": []})()


def block_gate(**kwargs):
    return type("Verdict", (), {"allow": False, "tripped": ["daily.realized_plus_unrealized_loss_usd"]})()


def signal(symbol):
    return NightlySignal(symbol, "long")


def position_context(symbol):
    return {
        "avg_dollar_volume_20d": 1_000_000,
        "dollar_volume_today": 500_000,
        "vwap_today": 100,
        "day_high": 103,
        "day_low": 99,
    }


def test_market_is_open_respects_calendar_window():
    calendar = MarketCalendar(NOW - timedelta(minutes=5), NOW + timedelta(minutes=5), True)

    assert market_is_open(calendar, NOW)
    assert not market_is_open(calendar, NOW + timedelta(hours=1))
    assert not market_is_open(MarketCalendar(None, None, False), NOW)


def test_intraday_tick_skips_outside_market_hours():
    db = engine()
    provider = FakeProvider(MarketCalendar(NOW - timedelta(hours=3), NOW - timedelta(hours=1), True))

    result = intraday_tick(now=NOW, provider=provider, engine=db, kill_switch_gate=allow_gate)

    assert result["status"] == "skipped"
    assert result["reason"] == "market_closed"
    assert provider.quotes == []


def test_intraday_tick_skips_when_kill_switch_active():
    db = engine()
    provider = FakeProvider()

    result = intraday_tick(now=NOW, provider=provider, engine=db, kill_switch_gate=block_gate)

    assert result["status"] == "skipped"
    assert result["reason"] == "kill_switch_active"
    assert provider.quotes == []


def test_intraday_tick_writes_decision_rows_for_scoped_symbols():
    db = engine()
    provider = FakeProvider()

    result = intraday_tick(
        now=NOW,
        selected_date=NOW.date(),
        provider=provider,
        engine=db,
        scope_loader=lambda *args, **kwargs: ["TSLA", "NVDA"],
        nightly_signal_loader=signal,
        position_context_loader=position_context,
        kill_switch_gate=allow_gate,
    )

    assert result["status"] == "ok"
    assert result["decisions_written"] == 2
    with db.connect() as conn:
        rows = conn.execute(select(intraday_decisions.c.symbol, intraday_decisions.c.verdict)).all()
    assert rows == [("TSLA", "allow_long"), ("NVDA", "allow_long")]
    assert provider.bars == [("TSLA", "5Min", 12), ("NVDA", "5Min", 12)]


def test_intraday_tick_records_data_unavailable_for_symbol_fetch_failure():
    db = engine()
    provider = FakeProvider(fail_symbols={"NVDA"})

    result = intraday_tick(
        now=NOW,
        selected_date=NOW.date(),
        provider=provider,
        engine=db,
        scope_loader=lambda *args, **kwargs: ["TSLA", "NVDA"],
        nightly_signal_loader=signal,
        position_context_loader=position_context,
        kill_switch_gate=allow_gate,
    )

    assert result["decisions_written"] == 2
    with db.connect() as conn:
        rows = conn.execute(select(intraday_decisions.c.symbol, intraday_decisions.c.verdict).order_by(intraday_decisions.c.symbol)).all()
    assert rows == [("NVDA", "data_unavailable"), ("TSLA", "allow_long")]


def test_production_provider_imports_are_kill_switch_gated():
    offenders = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "stockml.intraday.provider" not in text:
            continue
        if path.name == "provider.py":
            continue
        if "from stockml.intraday import kill_switch" not in text and "import stockml.intraday.kill_switch" not in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []
