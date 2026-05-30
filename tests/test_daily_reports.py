from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine, insert, select

from stockml.db.schema import (
    autopilot_open_log,
    create_all,
    daily_report_runs,
    intraday_candidate_snapshots,
    intraday_promotion_log,
    kill_switch_events,
    position_events,
    rotation_recommendation_log,
)
from stockml.reports.daily import build_daily_report, dashboard_report_card, get_or_build_report, report_csv, report_index


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def test_daily_report_builds_all_sections_and_persists_row():
    engine = _engine()
    day = date(2026, 5, 12)
    stamp = datetime(2026, 5, 12, 20, 30, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            insert(position_events),
            [
                {
                    "position_id": "paper:AAA",
                    "event_at": stamp,
                    "event_type": "submitted",
                    "source": "paper_autopilot",
                    "details": {"symbol": "AAA", "action": "close", "notional": 100, "account_equity": 1000, "unrealized_pnl": 0},
                },
                {
                    "position_id": "paper:AAA",
                    "event_at": stamp,
                    "event_type": "filled",
                    "source": "paper_autopilot",
                    "details": {"symbol": "AAA", "strategy_stream": "same_day_momentum", "realized_pnl": 12.5, "return_pct": 1.25, "account_equity": 1030, "unrealized_pnl": 5},
                },
            ],
        )
        snapshot_id = conn.execute(
            insert(intraday_candidate_snapshots).values(
                snapshot_at=stamp,
                bar_close_at=stamp,
                symbol="CSTL",
                nightly_score=0.7,
                nightly_bias="long",
                is_held=False,
                status="ok",
                details={},
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(intraday_promotion_log).values(
                logged_at=stamp,
                snapshot_id=snapshot_id,
                symbol="CSTL",
                verdict="promote_to_selection_strong",
                nightly_score=0.7,
                intraday_adjustment=0.02,
                promotion_score=0.72,
                contributing=["trend"],
            )
        )
        conn.execute(
            insert(autopilot_open_log).values(
                logged_at=stamp,
                symbol="CSTL",
                promotion_score=0.72,
                size_usd=100,
                verdict="opened",
                order_id="order-1",
                details={"strategy_stream": "same_day_momentum"},
            )
        )
        conn.execute(
            insert(rotation_recommendation_log).values(
                logged_at=stamp,
                replace_symbol="AAA",
                with_symbol="CSTL",
                promotion_score=0.72,
                held_score=0.5,
                score_delta=0.22,
                reason="HIGHER_PROMOTION_SCORE",
                verdict="proposed",
                details={},
            )
        )
        conn.execute(
            insert(kill_switch_events).values(
                switch_name="daily.realized_plus_unrealized_loss_usd",
                event_type="tripped",
                occurred_at=stamp,
                payload={"current": -40},
            )
        )

    report = build_daily_report(day, engine=engine, now=stamp)

    assert report["session_date"] == "2026-05-12"
    assert report["sections"]["account_state"]["total_pnl"] == 17.5
    assert report["sections"]["trading_activity"]["orders_submitted"] == 2
    assert report["sections"]["autopilot_actions"]["auto_opens"]["count"] == 1
    assert report["sections"]["stream_attribution"]["same_day_momentum"]["auto_opens_count"] == 1
    assert report["sections"]["stream_attribution"]["same_day_momentum"]["realized_pnl"] == 12.5
    assert "multi_day_forecast" in report["sections"]["stream_attribution"]
    assert report["sections"]["candidate_flow"]["promotions_to_selection_strong"] == 1
    assert report["sections"]["missed_opportunities"] == []
    assert {row["code"] for row in report["sections"]["next_day_recommendations"]} == {"KILL_SWITCH_REVIEW", "ROTATION_REVIEW"}
    with engine.connect() as conn:
        row = conn.execute(select(daily_report_runs)).mappings().one()
    assert row["session_date"] == day
    assert row["total_pnl"] == 17.5
    assert row["total_trades"] == 1


def test_daily_report_tracks_missed_strong_promotions_and_exports_csv():
    engine = _engine()
    day = date(2026, 5, 12)
    stamp = datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        snapshot_id = conn.execute(
            insert(intraday_candidate_snapshots).values(
                snapshot_at=stamp,
                bar_close_at=stamp,
                symbol="MISS",
                nightly_score=0.7,
                nightly_bias="long",
                is_held=False,
                status="ok",
                details={},
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(intraday_promotion_log).values(
                logged_at=stamp,
                snapshot_id=snapshot_id,
                symbol="MISS",
                verdict="promote_to_selection_strong",
                promotion_score=0.8,
                contributing=[],
            )
        )

    report = build_daily_report(day, engine=engine)
    csv_text = report_csv(report)

    assert report["sections"]["missed_opportunities"][0]["symbol"] == "MISS"
    assert report["sections"]["should_have_done"][0]["symbol"] == "MISS"
    assert "account_state,total_pnl,0.0" in csv_text


def test_report_index_and_get_or_build_read_persisted_payload():
    engine = _engine()
    day = date(2026, 5, 12)
    build_daily_report(day, engine=engine, now=datetime(2026, 5, 12, 20, 30, tzinfo=timezone.utc))

    rows = report_index(engine=engine)
    report = get_or_build_report(day, engine=engine)

    assert rows[0]["session_date"] == "2026-05-12"
    assert report["session_date"] == "2026-05-12"


def test_dashboard_report_card_returns_latest_or_today_links():
    engine = _engine()
    empty = dashboard_report_card(engine=engine, today=date(2026, 5, 13))
    assert empty["has_report"] is False
    assert empty["csv_url"] == "/reports/daily/2026-05-13.csv?refresh=1"

    build_daily_report(date(2026, 5, 12), engine=engine, now=datetime(2026, 5, 12, 20, 30, tzinfo=timezone.utc))
    card = dashboard_report_card(engine=engine)
    assert card["has_report"] is True
    assert card["view_url"] == "/reports/daily/2026-05-12"
    assert card["csv_url"] == "/reports/daily/2026-05-12.csv"
