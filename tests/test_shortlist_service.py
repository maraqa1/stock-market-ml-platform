import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy import create_engine, select

from portal.services.shortlist import get_for_date
from stockml.db.schema import create_all, shortlist_snapshots
from stockml.trading.shortlist_snapshots import write_shortlist_snapshot


def _root() -> Path:
    root = Path(".pytest_workspace") / f"shortlist_{uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_candidate_file(root: Path, stamp: str = "20260509_020000") -> Path:
    path = root / "data" / "portal_outputs" / f"08_alpaca_paper_candidate_pool_{stamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "candidate_rank": 1,
                "symbol": "AAA",
                "company": "Alpha Inc.",
                "sector": "Technology",
                "trade_action": "Long",
                "risk_adjusted_score": 0.84,
                "expected_trade_return": 0.021,
                "order_eligible": "true",
                "trade_quality_status": "approved",
            },
            {
                "candidate_rank": 2,
                "symbol": "BBB",
                "company": "Beta Corp.",
                "sector": "Healthcare",
                "trade_action": "Short",
                "risk_adjusted_score": 0.41,
                "expected_trade_return": -0.014,
                "order_eligible": "false",
                "trade_quality_status": "rejected",
                "trade_quality_reason": "risk_adjusted_score_below_threshold",
            },
            {
                "candidate_rank": 3,
                "symbol": "CCC",
                "company": "Core Co.",
                "sector": "Technology",
                "trade_action": "Neutral",
                "risk_adjusted_score": 0.12,
                "expected_trade_return": 0.0,
                "order_eligible": "false",
                "trade_quality_status": "rejected",
                "trade_quality_reason": "neutral_signal",
            },
        ]
    ).to_csv(path, index=False)
    mtime = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (mtime, mtime))
    return path


def test_shortlist_service_filters_artifact_rows_by_bias_sector_and_basket():
    root = _root()
    try:
        _write_candidate_file(root)

        payload = get_for_date(root, "2026-05-09", {})
        assert payload["total_candidates"] == 3
        assert [row["symbol"] for row in payload["rows"]] == ["AAA", "BBB", "CCC"]

        assert [row["symbol"] for row in get_for_date(root, "2026-05-09", {"bias": "long"})["rows"]] == ["AAA"]
        assert [row["symbol"] for row in get_for_date(root, "2026-05-09", {"sector": "Technology"})["rows"]] == ["AAA", "CCC"]
        assert [row["symbol"] for row in get_for_date(root, "2026-05-09", {"in_basket": "yes"})["rows"]] == ["AAA"]
        assert [row["symbol"] for row in get_for_date(root, "2026-05-09", {"in_basket": "no"})["rows"]] == ["BBB", "CCC"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_shortlist_service_returns_empty_payload_for_date_without_run():
    root = _root()
    try:
        _write_candidate_file(root)
        payload = get_for_date(root, "1999-01-01", {})
        assert payload["selected_date"] == "1999-01-01"
        assert payload["rows"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_shortlist_snapshot_writer_upserts_rows_self_contained():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    first = pd.DataFrame(
        [
            {
                "candidate_rank": 1,
                "symbol": "AAA",
                "trade_action": "Long",
                "risk_adjusted_score": 0.5,
                "expected_trade_return": 0.01,
                "order_eligible": True,
                "trade_quality_status": "approved",
            }
        ]
    )
    second = first.assign(risk_adjusted_score=0.75, trade_quality_status="trimmed")

    assert write_shortlist_snapshot("2026-05-09-A", first, target=engine) == 1
    assert write_shortlist_snapshot("2026-05-09-A", second, target=engine) == 1

    with engine.connect() as conn:
        rows = conn.execute(select(shortlist_snapshots)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["score"] == 0.75
    assert rows[0]["in_basket"] is True

