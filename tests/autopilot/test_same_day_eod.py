from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine, select

from stockml.autopilot.eod import EODConfig, run_eod_tick, select_flatten_targets, select_trim_targets, verify_overnight_state
from stockml.db.schema import create_all, kill_switch_events
from stockml.intraday import kill_switch
from stockml.intraday.features import Bar, Quote
from stockml.intraday.provider import MarketCalendar
from stockml.same_day.feature_worker import feature_tick


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)


def _engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def _same_day(symbol: str = "DAY") -> dict[str, object]:
    return {
        "symbol": symbol,
        "strategy_stream": "same_day_momentum",
        "must_flatten_at_eod": True,
        "unrealized_plpc": 0.03,
        "age_days": 0,
    }


def _multi_day(symbol: str = "SWING", **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "strategy_stream": "multi_day_forecast",
        "must_flatten_at_eod": False,
        "unrealized_plpc": 0.03,
        "age_days": 1,
    }
    row.update(extra)
    return row


def test_same_day_position_flattens_at_t_minus_5():
    calls = []

    result = run_eod_tick(
        pd.DataFrame([_same_day()]),
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=True),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert select_flatten_targets(pd.DataFrame([_same_day()]), config=EODConfig(), now=datetime(2026, 5, 12, 15, 56, tzinfo=ET))[0]["symbol"] == "DAY"
    assert result["eod_flatten_submitted"] == 1
    assert calls == [("DAY", "close")]


def test_multi_day_position_not_in_flatten():
    calls = []

    result = run_eod_tick(
        pd.DataFrame([_multi_day()]),
        now=datetime(2026, 5, 12, 15, 56, tzinfo=ET),
        state={},
        config=EODConfig(holdover_allowed=False),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert select_flatten_targets(pd.DataFrame([_multi_day()]), config=EODConfig(), now=datetime(2026, 5, 12, 15, 56, tzinfo=ET)) == []
    assert result["eod_flatten_submitted"] == 0
    assert calls == []


def test_multi_day_position_in_trim_if_stale():
    stale = _multi_day("STALE", unrealized_plpc=-0.02, age_days=2)
    calls = []

    result = run_eod_tick(
        pd.DataFrame([stale]),
        now=datetime(2026, 5, 12, 15, 47, tzinfo=ET),
        state={},
        config=EODConfig(),
        close_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "ok"},
    )

    assert select_trim_targets(pd.DataFrame([stale]), config=EODConfig(), now=datetime(2026, 5, 12, 15, 47, tzinfo=ET))[0]["symbol"] == "STALE"
    assert result["eod_flatten_submitted"] == 1
    assert calls == [("STALE", "close")]


class _Provider:
    def fetch_market_calendar(self, selected: date):
        return MarketCalendar(NOW - timedelta(hours=2), NOW + timedelta(hours=5), True)

    def fetch_quote(self, symbol: str):
        return Quote(symbol=symbol, bid=99.9, ask=100.0, last_price=100.0, quote_ts=NOW - timedelta(minutes=5), fetched_at=NOW)

    def fetch_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 24):
        return [
            Bar(timestamp=NOW - timedelta(minutes=5 * idx), open=100 - idx, high=101 - idx, low=99 - idx, close=100.2 - idx, volume=10_000, vwap=100.1 - idx)
            for idx in range(20, 0, -1)
        ]


def _allow_gate(**kwargs):
    return kill_switch.KillSwitchVerdict(True, [], NOW, False, None, None)


def test_overnight_kill_switch_blocks_same_day_next_session():
    db = _engine()

    payload = verify_overnight_state(pd.DataFrame([_same_day("DAY")]), now=NOW, engine=db)
    result = feature_tick(
        now=NOW,
        selected_date=NOW.date(),
        provider=_Provider(),
        engine=db,
        universe_loader=lambda selected: ["AAA"],
        market_context_loader=lambda stamp: {"open_at": NOW - timedelta(hours=2), "close_at": NOW + timedelta(hours=5)},
        symbol_context_loader=lambda symbol: {"avg_dollar_volume_20d": 50_000_000},
        kill_switch_gate=_allow_gate,
    )

    assert payload["same_day_count"] == 1
    assert payload["symbols"] == ["DAY"]
    assert result["status"] == "skipped"
    assert result["reason"] == "OVERNIGHT_POSITIONS_SAME_DAY"


def test_overnight_multi_day_only_does_not_block_stream():
    db = _engine()

    payload = verify_overnight_state(pd.DataFrame([_multi_day("SWING")]), now=NOW, engine=db)
    gate = kill_switch.gate(action="evaluate", engine=db, now=NOW)
    result = feature_tick(
        now=NOW,
        selected_date=NOW.date(),
        provider=_Provider(),
        engine=db,
        universe_loader=lambda selected: ["AAA"],
        market_context_loader=lambda stamp: {"open_at": NOW - timedelta(hours=2), "close_at": NOW + timedelta(hours=5)},
        symbol_context_loader=lambda symbol: {"avg_dollar_volume_20d": 50_000_000},
        kill_switch_gate=_allow_gate,
    )

    with db.connect() as conn:
        rows = conn.execute(select(kill_switch_events.c.switch_name, kill_switch_events.c.payload)).all()

    assert payload["same_day_count"] == 0
    assert payload["multi_day_count"] == 1
    assert rows[-1][0] == kill_switch.OVERNIGHT_POSITIONS
    assert gate.allow is True
    assert result["status"] == "ok"
    assert result["features_written"] == 1
