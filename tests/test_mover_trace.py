from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import insert

from stockml.db.connection import get_engine
from stockml.db.schema import autopilot_open_log, create_all, intraday_candidate_snapshots, intraday_promotion_log
from stockml.trading.mover_trace import trace_intraday_movers


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_trace_flags_model_no_decision_before_candidate_pool(tmp_path: Path):
    _write(
        tmp_path / "data" / "model_outputs" / "model_predictions_latest.csv",
        [{"ticker": "SNOW", "trade_action": "No Decision", "risk_adjusted_score": 0.1, "expected_trade_return": 0.2}],
    )
    _write(tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv", [])

    frame, path = trace_intraday_movers(["SNOW"], root=tmp_path, write=True, stamp="stamp")

    assert path == tmp_path / "data" / "trading" / "mover_trace" / "mover_trace_stamp.csv"
    assert frame.loc[0, "trace_reason"] == "model_no_decision_not_candidate"
    assert frame.loc[0, "model_trade_action"] == "No Decision"


def test_trace_reports_candidate_rejection_reason(tmp_path: Path):
    _write(
        tmp_path / "data" / "model_outputs" / "model_predictions_latest.csv",
        [{"ticker": "AMPX", "trade_action": "Long"}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
        [{"symbol": "AMPX", "trade_quality_status": "rejected", "trade_quality_reason": "risk_gate_failed"}],
    )

    frame, _ = trace_intraday_movers(["AMPX"], root=tmp_path, write=False)

    assert frame.loc[0, "trace_reason"] == "candidate_rejected:risk_gate_failed"
    assert frame.loc[0, "candidate_status"] == "rejected"


def test_trace_prefers_latest_autopilot_block(tmp_path: Path):
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)
    now = datetime(2026, 5, 28, 15, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        snapshot_id = conn.execute(
            insert(intraday_candidate_snapshots).values(
                snapshot_at=now,
                bar_close_at=now,
                symbol="BBY",
                nightly_bias="long",
                is_held=False,
                status="ok",
                last_price=76.56,
                dollar_volume_today=676_350_000,
                details={},
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(intraday_promotion_log).values(
                logged_at=now,
                snapshot_id=snapshot_id,
                symbol="BBY",
                verdict="promote_to_selection_strong",
                promotion_score=0.91,
            )
        )
        conn.execute(
            insert(autopilot_open_log).values(
                logged_at=now,
                symbol="BBY",
                promotion_score=0.91,
                verdict="blocked",
                block_reason="model_evidence_missing",
                details={},
            )
        )

    frame, _ = trace_intraday_movers(["BBY"], root=tmp_path, engine=engine, write=False)

    assert frame.loc[0, "trace_reason"] == "autopilot_blocked:model_evidence_missing"
    assert frame.loc[0, "promotion_verdict"] == "promote_to_selection_strong"
