from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from stockml.db.schema import create_all, intraday_candidate_snapshots, pipeline_runs, shortlist_snapshots
from stockml.intraday.features import Bar, Quote
from stockml.intraday.provider import MarketCalendar
from stockml.intraday.refresh import build_snapshot, candidate_refresh_tick, prune_old_snapshots, write_snapshot
from stockml.intraday.scope import scope_rows_for_today


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeProvider:
    def __init__(self, calendar=None, fail_symbols=None, unavailable_symbols=None):
        self.calendar = calendar or MarketCalendar(NOW - timedelta(hours=1), NOW + timedelta(hours=5), True)
        self.fail_symbols = set(fail_symbols or [])
        self.unavailable_symbols = set(unavailable_symbols or [])
        self.quotes = []
        self.bars = []

    def fetch_market_calendar(self, selected: date):
        return self.calendar

    def fetch_quote(self, symbol: str):
        self.quotes.append(symbol)
        if symbol in self.fail_symbols:
            raise RuntimeError("quote_failed")
        if symbol in self.unavailable_symbols:
            return None
        return Quote(symbol=symbol, bid=99.95, ask=100.05, bid_size=100, ask_size=120, last_price=100, quote_ts=NOW - timedelta(seconds=1), fetched_at=NOW)

    def fetch_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 12):
        self.bars.append((symbol, timeframe, limit))
        if symbol in self.unavailable_symbols:
            return None
        return [
            Bar(
                open=99 + i * 0.2,
                high=100 + i * 0.2,
                low=98 + i * 0.2,
                close=99 + i * 0.2,
                volume=1000 + i * 10,
                vwap=99 + i * 0.1,
                timestamp=NOW - timedelta(minutes=(limit - i - 1) * 5),
            )
            for i in range(limit)
        ]


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def allow_gate(**kwargs):
    return type("Verdict", (), {"allow": True, "tripped": []})()


def block_gate(**kwargs):
    return type("Verdict", (), {"allow": False, "tripped": ["daily.realized_plus_unrealized_loss_usd"]})()


def seed_shortlist(db):
    with db.begin() as conn:
        conn.execute(insert(pipeline_runs).values(run_id="run-1", started_at=NOW, status="success"))
        conn.execute(
            insert(shortlist_snapshots),
            [
                {"run_id": "run-1", "rank": 1, "symbol": "TSLA", "bias": "long", "score": 0.8, "in_basket": True},
                {"run_id": "run-1", "rank": 2, "symbol": "NVDA", "bias": "short", "score": 0.7, "in_basket": False},
            ],
        )


def test_scope_rows_include_shortlist_metadata_and_open_positions():
    db = engine()
    seed_shortlist(db)

    rows = scope_rows_for_today(NOW.date(), positions_loader=lambda: [{"symbol": "AAPL"}, {"symbol": "TSLA"}], engine=db)

    by_symbol = {row["symbol"]: row for row in rows}
    assert sorted(by_symbol) == ["AAPL", "NVDA", "TSLA"]
    assert by_symbol["TSLA"]["score"] == 0.8
    assert by_symbol["TSLA"]["bias"] == "long"
    assert by_symbol["TSLA"]["is_held"] is True
    assert by_symbol["AAPL"]["source"] == "open_position"
    assert by_symbol["AAPL"]["is_held"] is True


def test_scope_rows_prefer_latest_broad_shortlist_over_one_row_artifact():
    db = engine()
    with db.begin() as conn:
        conn.execute(insert(pipeline_runs).values(run_id="broad-run", started_at=NOW - timedelta(minutes=30), status="success"))
        conn.execute(insert(pipeline_runs).values(run_id="single-run", started_at=NOW, status="success"))
        conn.execute(
            insert(shortlist_snapshots),
            [
                {"run_id": "broad-run", "rank": rank, "symbol": f"SYM{rank:02d}", "bias": "long", "score": 1 - rank / 100, "in_basket": rank <= 5}
                for rank in range(1, 31)
            ],
        )
        conn.execute(insert(shortlist_snapshots).values(run_id="single-run", rank=1, symbol="ONLY", bias="long", score=0.9, in_basket=True))

    rows = scope_rows_for_today(NOW.date(), engine=db)

    symbols = {row["symbol"] for row in rows}
    assert len(rows) == 30
    assert "ONLY" not in symbols
    assert "SYM01" in symbols


def test_build_snapshot_computes_intraday_fields_from_quote_and_bars():
    provider = FakeProvider()
    row = {"symbol": "TSLA", "bias": "long", "score": 0.8, "is_held": True, "avg_dollar_volume_20d": 1_000_000}

    snapshot = build_snapshot(row, provider.fetch_quote("TSLA"), provider.fetch_bars("TSLA"), {"market_aligned": True, "sector_etf_trend_5m_pct": 0.4}, now=NOW)

    assert snapshot.symbol == "TSLA"
    assert snapshot.status == "ok"
    assert snapshot.nightly_score == 0.8
    assert snapshot.nightly_bias == "long"
    assert snapshot.is_held is True
    assert round(snapshot.spread_bps or 0, 2) == 10.0
    assert snapshot.quote_age_sec == 1
    assert snapshot.trend_5m_pct is not None
    assert snapshot.trend_15m_pct is not None
    assert snapshot.trend_30m_pct is not None
    assert snapshot.distance_from_vwap_bps is not None
    assert snapshot.market_aligned is True


def test_candidate_refresh_skips_outside_market_hours():
    db = engine()
    provider = FakeProvider(MarketCalendar(NOW - timedelta(hours=3), NOW - timedelta(hours=1), True))

    result = candidate_refresh_tick(now=NOW, provider=provider, engine=db, kill_switch_gate=allow_gate)

    assert result["status"] == "skipped"
    assert result["reason"] == "market_closed"
    assert provider.quotes == []


def test_candidate_refresh_skips_when_kill_switch_active():
    db = engine()
    provider = FakeProvider()

    result = candidate_refresh_tick(now=NOW, provider=provider, engine=db, kill_switch_gate=block_gate)

    assert result["status"] == "skipped"
    assert result["reason"] == "kill_switch_active"
    assert provider.quotes == []
    with db.connect() as conn:
        assert conn.execute(select(intraday_candidate_snapshots)).all() == []


def test_candidate_refresh_writes_one_snapshot_per_symbol_and_is_idempotent():
    db = engine()
    provider = FakeProvider()
    scope = lambda *args, **kwargs: [
        {"symbol": "TSLA", "bias": "long", "score": 0.8, "is_held": False},
        {"symbol": "NVDA", "bias": "short", "score": 0.7, "is_held": True},
    ]

    first = candidate_refresh_tick(now=NOW, provider=provider, engine=db, scope_loader=scope, kill_switch_gate=allow_gate)
    second = candidate_refresh_tick(now=NOW, provider=provider, engine=db, scope_loader=scope, kill_switch_gate=allow_gate)

    assert first["snapshots_written"] == 2
    assert second["snapshots_written"] == 0
    with db.connect() as conn:
        rows = conn.execute(select(intraday_candidate_snapshots.c.symbol, intraday_candidate_snapshots.c.status).order_by(intraday_candidate_snapshots.c.symbol)).all()
    assert rows == [("NVDA", "ok"), ("TSLA", "ok")]


def test_candidate_refresh_records_data_unavailable_and_provider_errors():
    db = engine()
    provider = FakeProvider(fail_symbols={"NVDA"}, unavailable_symbols={"TSLA"})
    scope = lambda *args, **kwargs: [
        {"symbol": "TSLA", "bias": "long", "score": 0.8, "is_held": False},
        {"symbol": "NVDA", "bias": "short", "score": 0.7, "is_held": False},
    ]

    result = candidate_refresh_tick(now=NOW, provider=provider, engine=db, scope_loader=scope, kill_switch_gate=allow_gate)

    assert result["snapshots_written"] == 2
    with db.connect() as conn:
        rows = conn.execute(select(intraday_candidate_snapshots.c.symbol, intraday_candidate_snapshots.c.status).order_by(intraday_candidate_snapshots.c.symbol)).all()
    assert rows == [("NVDA", "provider_error"), ("TSLA", "data_unavailable")]


def test_write_snapshot_rejects_duplicate_symbol_bar_close():
    db = engine()
    provider = FakeProvider()
    row = {"symbol": "TSLA", "bias": "long", "score": 0.8, "is_held": False}
    snapshot = build_snapshot(row, provider.fetch_quote("TSLA"), provider.fetch_bars("TSLA"), now=NOW)

    assert write_snapshot(snapshot, engine=db) is True
    assert write_snapshot(snapshot, engine=db) is False


def test_write_snapshot_checks_numeric_column_compatibility(monkeypatch):
    calls = []
    monkeypatch.setattr("stockml.intraday.refresh.ensure_intraday_candidate_snapshot_float_columns", lambda db: calls.append(db))
    db = engine()
    provider = FakeProvider()
    row = {"symbol": "AG", "bias": "long", "score": 40.924, "is_held": False}
    snapshot = build_snapshot(row, provider.fetch_quote("TSLA"), provider.fetch_bars("TSLA"), now=NOW)

    assert write_snapshot(snapshot, engine=db) is True
    assert calls == [db]


def test_prune_old_snapshots_removes_rows_older_than_retention_window():
    db = engine()
    provider = FakeProvider()
    row = {"symbol": "TSLA", "bias": "long", "score": 0.8, "is_held": False}
    old_snapshot = build_snapshot(row, provider.fetch_quote("TSLA"), provider.fetch_bars("TSLA"), now=NOW - timedelta(days=8))
    new_snapshot = build_snapshot({**row, "symbol": "NVDA"}, provider.fetch_quote("NVDA"), provider.fetch_bars("NVDA"), now=NOW)
    assert write_snapshot(old_snapshot, engine=db)
    assert write_snapshot(new_snapshot, engine=db)

    assert prune_old_snapshots(engine=db, now=NOW, retention_days=7) == 1
    with db.connect() as conn:
        rows = conn.execute(select(intraday_candidate_snapshots.c.symbol)).all()
    assert rows == [("NVDA",)]


def test_candidate_refresh_contains_no_order_submission_calls():
    text = (PROJECT_ROOT / "src" / "stockml" / "intraday" / "refresh.py").read_text(encoding="utf-8")

    assert "submit_order" not in text
