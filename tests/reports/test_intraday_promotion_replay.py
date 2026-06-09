from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, insert

from stockml.db.schema import create_all, intraday_candidate_snapshots, intraday_promotion_log
from stockml.reports.intraday_promotion_replay import build_intraday_promotion_replay


NOW = datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc)


def _engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def _snapshot(symbol: str, bias: str = "long") -> dict:
    return {
        "snapshot_at": NOW,
        "bar_close_at": NOW,
        "symbol": symbol,
        "nightly_score": 0.61 if bias == "long" else -0.61,
        "nightly_bias": bias,
        "is_held": False,
        "bid": 99.9,
        "ask": 100.1,
        "last_price": 100.0,
        "spread_bps": 20.0,
        "quote_age_sec": 1,
        "dollar_volume_today": 1_000_000.0,
        "liquidity_ratio": 1.0,
        "trend_5m_pct": 1.2,
        "trend_15m_pct": 2.0,
        "trend_30m_pct": 3.0,
        "vwap_today": 99.5,
        "distance_from_vwap_bps": 50.0,
        "intraday_range_position": 0.7,
        "volatility_burst": False,
        "sector_etf_trend_5m_pct": 0.6,
        "market_aligned": True,
        "status": "ok",
        "details": {"volume_ratio": 1.5},
    }


def _insert_promotion(db, symbol: str, *, verdict: str, bias: str = "long", score: float = 0.70) -> None:
    with db.begin() as conn:
        result = conn.execute(insert(intraday_candidate_snapshots).values(**_snapshot(symbol, bias)))
        snapshot_id = int(result.inserted_primary_key[0])
        conn.execute(
            insert(intraday_promotion_log).values(
                logged_at=NOW,
                snapshot_id=snapshot_id,
                symbol=symbol,
                verdict=verdict,
                nightly_score=0.61 if bias == "long" else -0.61,
                intraday_adjustment=0.09,
                promotion_score=score,
                contributing=["score_trend_5m_bonus"],
            )
        )


def test_replay_flags_false_promotions_and_writes_outputs(tmp_path: Path):
    db = _engine()
    _insert_promotion(db, "AAA", verdict="promote_to_selection_strong")
    _insert_promotion(db, "BBB", verdict="promote_to_selection")
    _insert_promotion(db, "CCC", verdict="watch")
    gold = tmp_path / "gold.csv"
    pd.DataFrame(
        [
            {"date": "2026-06-08", "ticker": "AAA", "forward_5d_return": 0.02, "forward_5d_alpha_vs_sector": 0.01, "sector": "Tech"},
            {"date": "2026-06-08", "ticker": "BBB", "forward_5d_return": -0.02, "forward_5d_alpha_vs_sector": -0.03, "sector": "Tech"},
            {"date": "2026-06-08", "ticker": "CCC", "forward_5d_return": -0.05, "forward_5d_alpha_vs_sector": -0.05, "sector": "Tech"},
        ]
    ).to_csv(gold, index=False)

    outputs = build_intraday_promotion_replay(engine=db, gold_file=gold, output_dir=tmp_path, stamp="20260608_150000", now=NOW)

    replay = pd.read_csv(outputs.replay_path)
    summary = pd.read_csv(outputs.summary_path)
    assert outputs.missing_inputs == ()
    assert outputs.replay_rows == 3
    assert replay.loc[replay["symbol"].eq("BBB"), "false_promotion"].iloc[0] == True
    assert replay.loc[replay["symbol"].eq("BBB"), "false_promotion_reason"].iloc[0] == "negative_directional_forward_return"
    assert summary["false_promotion_count"].sum() == 1
    assert "read-only" in outputs.markdown_path.read_text(encoding="utf-8")


def test_replay_marks_missing_outcomes(tmp_path: Path):
    db = _engine()
    _insert_promotion(db, "AAA", verdict="promote_to_selection_strong")
    gold = tmp_path / "gold.csv"
    pd.DataFrame([{"date": "2026-06-08", "ticker": "ZZZ", "forward_5d_return": 0.02}]).to_csv(gold, index=False)

    outputs = build_intraday_promotion_replay(engine=db, gold_file=gold, output_dir=tmp_path, stamp="missing")

    replay = pd.read_csv(outputs.replay_path)
    assert replay["promotion_replay_status"].iloc[0] == "missing_outcome"
    assert replay["false_promotion_reason"].iloc[0] == "missing_forward_outcome"


def test_replay_writes_missing_data_section_when_no_logs(tmp_path: Path):
    outputs = build_intraday_promotion_replay(engine=_engine(), gold_file=tmp_path / "missing.csv", output_dir=tmp_path, stamp="empty")

    replay = pd.read_csv(outputs.replay_path)
    assert outputs.missing_inputs == ("intraday_promotion_log", "gold_forward_outcomes")
    assert replay["status"].iloc[0] == "missing_data"
