from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from portal.app import create_app
from stockml.db.schema import create_all, intraday_candidate_snapshots, intraday_promotion_log
from stockml.intraday.promotion_score import evaluate_snapshot, explain_latest_snapshot, intraday_adjustment, record_promotion_decision, score_unscored_snapshots


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def row(**overrides):
    base = {
        "id": 1,
        "snapshot_at": NOW,
        "bar_close_at": NOW,
        "symbol": "TSLA",
        "nightly_score": 0.62,
        "nightly_bias": "long",
        "is_held": False,
        "bid": 99.95,
        "ask": 100.05,
        "last_price": 100.0,
        "spread_bps": 10.0,
        "quote_age_sec": 1,
        "dollar_volume_today": 500_000,
        "liquidity_ratio": 0.2,
        "trend_5m_pct": 1.2,
        "trend_15m_pct": 2.2,
        "trend_30m_pct": 3.0,
        "vwap_today": 99.0,
        "distance_from_vwap_bps": 101.0,
        "intraday_range_position": 0.75,
        "volatility_burst": False,
        "sector_etf_trend_5m_pct": 0.6,
        "market_aligned": True,
        "status": "ok",
        "details": {"volume_ratio": 1.6, "spy_intraday_trend_5m_pct": 0.1, "vix_regime": "normal"},
    }
    base.update(overrides)
    return base


def insert_snapshot(db, **overrides) -> int:
    payload = row(**overrides)
    payload.pop("id", None)
    with db.begin() as conn:
        result = conn.execute(insert(intraday_candidate_snapshots).values(**payload))
        return int(result.inserted_primary_key[0])


def test_long_confirmation_promotes_when_threshold_is_met():
    decision = evaluate_snapshot(row())

    assert decision.verdict == "promote_to_selection_strong"
    assert decision.block_reason is None
    assert 0 <= decision.promotion_score <= 1
    assert "long_trend_5m_positive" in decision.contributing


def test_confirmation_failure_returns_watch_not_block():
    decision = evaluate_snapshot(row(trend_5m_pct=-0.1))

    assert decision.verdict == "watch"
    assert decision.block_reason is None


def test_short_confirmation_mirrors_long_direction():
    decision = evaluate_snapshot(
        row(
            symbol="SQQQ",
            nightly_bias="short",
            nightly_score=-0.58,
            trend_5m_pct=-1.2,
            trend_15m_pct=-2.2,
            distance_from_vwap_bps=-20,
            intraday_range_position=0.25,
            sector_etf_trend_5m_pct=-0.6,
            details={"volume_ratio": 1.6, "spy_intraday_trend_5m_pct": -0.1, "vix_regime": "normal"},
        )
    )

    assert decision.verdict == "promote_to_selection_strong"
    assert "short_trend_5m_negative" in decision.contributing


def test_precheck_blocks_take_precedence_over_confirmation():
    decision = evaluate_snapshot(row(spread_bps=50.0))

    assert decision.verdict == "block"
    assert decision.block_reason == "wide_spread"


def test_short_promotion_uses_directional_signal_strength():
    decision = evaluate_snapshot(
        row(
            symbol="SQQQ",
            nightly_bias="short",
            nightly_score=-0.62,
            trend_5m_pct=-1.2,
            trend_15m_pct=-2.2,
            distance_from_vwap_bps=-20,
            intraday_range_position=0.25,
            sector_etf_trend_5m_pct=-0.6,
            details={"volume_ratio": 1.6, "spy_intraday_trend_5m_pct": -0.1, "vix_regime": "normal"},
        )
    )

    assert decision.verdict == "promote_to_selection_strong"
    assert decision.promotion_score > 0.6


def test_short_intraday_adjustment_rewards_short_direction():
    adjustment, contributing = intraday_adjustment(
        row(
            symbol="SQQQ",
            nightly_bias="short",
            trend_5m_pct=-1.2,
            trend_15m_pct=-2.2,
            intraday_range_position=0.25,
            sector_etf_trend_5m_pct=-0.6,
            distance_from_vwap_bps=-20,
            details={"volume_ratio": 1.6, "spy_intraday_trend_5m_pct": -0.1, "vix_regime": "normal"},
        )
    )

    assert adjustment > 0
    assert "score_trend_5m_bonus" in contributing
    assert "score_trend_15m_bonus" in contributing
    assert "score_range_bonus" in contributing
    assert "score_sector_bonus" in contributing


def test_short_intraday_adjustment_does_not_reward_long_direction():
    adjustment, contributing = intraday_adjustment(
        row(
            symbol="SQQQ",
            nightly_bias="short",
            trend_5m_pct=1.2,
            trend_15m_pct=2.2,
            intraday_range_position=0.75,
            sector_etf_trend_5m_pct=0.6,
            distance_from_vwap_bps=120,
            details={"volume_ratio": 1.0, "spy_intraday_trend_5m_pct": 0.1, "vix_regime": "normal"},
        )
    )

    assert adjustment < 0
    assert "score_trend_5m_bonus" not in contributing
    assert "score_trend_15m_bonus" not in contributing
    assert "score_range_bonus" not in contributing
    assert "score_sector_bonus" not in contributing
    assert "score_vwap_penalty" in contributing


def test_strong_candidate_gets_limited_spread_relaxation():
    decision = evaluate_snapshot(
        row(
            symbol="EMBC",
            nightly_score=0.6411,
            spread_bps=30.26,
            dollar_volume_today=1_913_491,
            trend_5m_pct=0.607,
            trend_15m_pct=1.2214,
            distance_from_vwap_bps=42.8,
            intraday_range_position=0.61,
            sector_etf_trend_5m_pct=0.0,
            details={"volume_ratio": 1.0, "spy_intraday_trend_5m_pct": 0.1, "vix_regime": "normal"},
        )
    )

    assert decision.verdict == "promote_to_selection"
    assert decision.block_reason is None
    assert "strong_candidate_spread_relaxed" in decision.contributing


def test_wide_spread_relaxation_requires_strong_candidate_quality():
    decision = evaluate_snapshot(
        row(
            nightly_score=0.3375,
            spread_bps=30.26,
            dollar_volume_today=1_913_491,
            trend_5m_pct=0.607,
            trend_15m_pct=1.2214,
            intraday_range_position=0.61,
        )
    )

    assert decision.verdict == "block"
    assert decision.block_reason == "wide_spread"


def test_short_strong_candidate_gets_limited_spread_relaxation_from_abs_score():
    decision = evaluate_snapshot(
        row(
            symbol="SQQQ",
            nightly_bias="short",
            nightly_score=-0.6411,
            spread_bps=30.26,
            dollar_volume_today=1_913_491,
            trend_5m_pct=-0.607,
            trend_15m_pct=-1.2214,
            distance_from_vwap_bps=-42.8,
            intraday_range_position=0.39,
            sector_etf_trend_5m_pct=0.0,
            details={"volume_ratio": 1.0, "spy_intraday_trend_5m_pct": -0.1, "vix_regime": "normal"},
        )
    )

    assert decision.verdict == "promote_to_selection"
    assert decision.block_reason is None
    assert "strong_candidate_spread_relaxed" in decision.contributing


def test_nightly_signal_missing_blocks():
    decision = evaluate_snapshot(row(nightly_bias="neutral"))

    assert decision.verdict == "block"
    assert decision.block_reason == "nightly_signal_dropped"


def test_score_is_bounded_to_zero_one():
    decision = evaluate_snapshot(row(nightly_score=0.99))

    assert decision.promotion_score == 1.0


def test_symbol_cooloff_blocks_recent_repromotion():
    decision = evaluate_snapshot(row(), recent_action_taken=True)

    assert decision.verdict == "block"
    assert decision.block_reason == "symbol_cooloff"


def test_score_unscored_snapshots_writes_one_log_per_snapshot_and_is_idempotent():
    db = engine()
    first_id = insert_snapshot(db, symbol="TSLA")
    second_id = insert_snapshot(db, symbol="NVDA", bar_close_at=NOW.replace(minute=5), nightly_score=0.4)

    first = score_unscored_snapshots(engine=db, now=NOW)
    second = score_unscored_snapshots(engine=db, now=NOW)

    assert first["snapshots_scored"] == 2
    assert second["snapshots_scored"] == 0
    with db.connect() as conn:
        rows = conn.execute(select(intraday_promotion_log.c.snapshot_id, intraday_promotion_log.c.verdict).order_by(intraday_promotion_log.c.snapshot_id)).all()
    assert [item[0] for item in rows] == [first_id, second_id]


def test_record_promotion_decision_reuses_existing_snapshot_log():
    db = engine()
    snapshot_id = insert_snapshot(db)
    decision = evaluate_snapshot(row())

    first = record_promotion_decision(snapshot_id, decision, engine=db, logged_at=NOW)
    second = record_promotion_decision(snapshot_id, decision, engine=db, logged_at=NOW)

    assert first == second
    with db.connect() as conn:
        assert len(conn.execute(select(intraday_promotion_log)).all()) == 1


def test_explain_latest_snapshot_evaluates_current_code_without_writing_log():
    db = engine()
    snapshot_id = insert_snapshot(
        db,
        symbol="EMBC",
        nightly_score=0.6411,
        spread_bps=29.81,
        dollar_volume_today=1_496_769,
        trend_5m_pct=0.1502,
        trend_15m_pct=0.9077,
        intraday_range_position=0.95,
        distance_from_vwap_bps=16.25,
        details={"volume_ratio": 1.0, "spy_intraday_trend_5m_pct": 0.1, "vix_regime": "normal"},
    )

    explanation = explain_latest_snapshot("EMBC", engine=db)

    assert explanation["snapshot_id"] == snapshot_id
    assert explanation["decision"].verdict == "promote_to_selection_strong"
    assert "strong_candidate_spread_relaxed" in explanation["decision"].contributing
    assert explanation["directional_score"] == 0.6411
    with db.connect() as conn:
        assert conn.execute(select(intraday_promotion_log)).all() == []


def test_trading_page_renders_intraday_promotion_zone(monkeypatch, tmp_path):
    def fake_context(root):
        return {
            "source": "database",
            "latest_tick": NOW.isoformat(),
            "counts": {"total": 1},
            "rows": [
                {
                    "symbol": "TSLA",
                    "is_held": False,
                    "nightly_score": 0.62,
                    "intraday_adjustment": 0.13,
                    "promotion_score": 0.75,
                    "verdict": "promote_to_selection_strong",
                    "block_reason": "",
                    "contributing": ["long_trend_5m_positive", "score_volume_bonus"],
                }
            ],
        }

    monkeypatch.setattr("portal.app.intraday_promotion_context", fake_context)
    app = create_app(tmp_path)
    app.config.update(TESTING=True)
    response = app.test_client().get("/trading")

    assert response.status_code == 200
    assert b"Intraday Promotion" in response.data
    assert b"TSLA" in response.data
    assert b"Promote To Selection Strong" in response.data


def test_intraday_promotion_code_contains_no_order_submission_calls():
    for name in ["promotion_score.py", "promotion_gate.py"]:
        text = (PROJECT_ROOT / "src" / "stockml" / "intraday" / name).read_text(encoding="utf-8")
        assert "submit_order" not in text
