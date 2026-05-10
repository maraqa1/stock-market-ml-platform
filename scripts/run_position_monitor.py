#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.agents.position_decision_engine import build_position_decisions, write_position_decisions
from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.trading.paper_trader import refresh_order_tracking
from stockml.trading.pnl_tracker import position_pnl_summary, write_pnl_summary
from stockml.trading.timer_settings import monitor_should_run
from stockml.trading.trade_journal import build_trade_journal, write_trade_journal


def _read_csv(path: Path | None) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False) if path and path.exists() else pd.DataFrame()


def _latest_portal(pattern: str) -> Path | None:
    return latest_file(PORTAL_OUTPUTS_DIR, pattern)


def main() -> int:
    ensure_data_dirs()
    should_run, cadence_reason = monitor_should_run()
    if not should_run:
        print(f"monitor_skipped: {cadence_reason}")
        return 0
    stamp = timestamp()
    refreshed = refresh_order_tracking()
    plan_path = _latest_portal("08_alpaca_paper_order_plan_*.csv")
    candidate_pool_path = _latest_portal("08_alpaca_paper_candidate_pool_*.csv")
    result_path = _latest_portal("08_alpaca_paper_order_results_*.csv")
    positions_path = Path(refreshed["positions_path"])
    plan = _read_csv(plan_path)
    candidate_pool = _read_csv(candidate_pool_path)
    results = _read_csv(result_path)
    positions = _read_csv(positions_path)
    fallback_signal_time = (
        datetime.fromtimestamp(plan_path.stat().st_mtime, tz=timezone.utc)
        if plan_path and plan_path.exists()
        else None
    )

    journal = build_trade_journal(plan, results)
    pnl = position_pnl_summary(positions)
    decisions = build_position_decisions(
        positions,
        plan,
        results,
        candidate_pool,
        now=datetime.now(timezone.utc),
        signal_ttl_minutes=10,
        fallback_signal_time=fallback_signal_time,
    )

    journal_path = write_trade_journal(journal, stamp)
    pnl_path = write_pnl_summary(pnl, stamp)
    decision_path = write_position_decisions(decisions, stamp)

    print(f"orders_tracked: {refreshed['orders_tracked']}")
    print(f"tracking_path: {refreshed['tracking_path']}")
    print(f"positions_path: {positions_path}")
    print(f"journal_rows: {len(journal)}")
    print(f"pnl_rows: {len(pnl)}")
    print(f"position_decisions: {len(decisions)}")
    print(f"journal_path: {journal_path}")
    print(f"pnl_path: {pnl_path}")
    print(f"decision_path: {decision_path}")
    print(f"cadence_reason: {cadence_reason}")
    if not decisions.empty:
        print(f"decision_counts: {decisions['decision'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
