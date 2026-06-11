from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.trading.intraday_handoff_trace import write_intraday_handoff_trace


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_handoff_trace_marks_selected_and_order_result(tmp_path: Path):
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260611_150000.csv",
        [
            {"symbol": "SNOW", "side": "buy", "risk_adjusted_score": 4.0, "trade_quality_status": "approved"},
            {"symbol": "VPG", "side": "buy", "risk_adjusted_score": 3.0, "trade_quality_status": "reduced"},
        ],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_20260611_150000.csv",
        [{"symbol": "SNOW", "side": "buy"}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_results_20260611_150000.csv",
        [{"symbol": "SNOW", "status": "submitted", "message": "accepted"}],
    )

    result = write_intraday_handoff_trace(root=tmp_path, stamp="20260611_150500", top_n=5)

    frame = pd.read_csv(result["path"])
    snow = frame[(frame["stage"].eq("candidate_pool")) & (frame["symbol"].eq("SNOW"))].iloc[0]
    vpg = frame[(frame["stage"].eq("candidate_pool")) & (frame["symbol"].eq("VPG"))].iloc[0]
    assert bool(snow["selected_in_order_plan"]) is True
    assert snow["order_result_status"] == "submitted"
    assert bool(vpg["selected_in_order_plan"]) is False
    assert Path(result["summary_path"]).exists()


def test_handoff_trace_includes_cycle_summary_rows(tmp_path: Path):
    result = write_intraday_handoff_trace(
        root=tmp_path,
        stamp="20260611_151000",
        refresh={"status": "ok", "snapshots_written": 200},
        scoring={"status": "ok", "snapshots_scored": 179, "verdict_counts": {"watch": 10}},
        forecast={"status": "ok", "rows": 100},
        autopilot={"phase": "tracking_orders", "autopilot_open_submitted": 0, "autopilot_open_notes": "open_orders_present"},
        snapshot={"status": "ok", "rows": 500},
    )

    frame = pd.read_csv(result["path"])
    assert {"candidate_refresh", "intraday_promotion", "paper_autopilot", "trading_snapshot_summary"}.issubset(set(frame["stage"]))
    autopilot = frame[frame["stage"].eq("paper_autopilot")].iloc[0]
    assert autopilot["reason"] == "open_orders_present"
