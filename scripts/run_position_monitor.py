#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.agents.position_decision_engine import build_position_decisions, write_position_decisions
from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.overnight_close_reprice import reprice_stale_overnight_close_orders
from stockml.trading.paper_trader import refresh_order_tracking
from stockml.trading.pnl_tracker import position_pnl_summary, write_pnl_summary
from stockml.trading.position_monitor_closes import execute_position_monitor_closes
from stockml.trading.timer_settings import monitor_should_run
from stockml.trading.trade_journal import build_trade_journal, write_trade_journal
from stockml.reports.closed_trades_attribution import write_reconstructed_closed_trades_attribution


def _read_csv(path: Path | None) -> pd.DataFrame:
    if not path or not path.exists() or path.stat().st_size <= 1:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame()


def _latest_portal(pattern: str) -> Path | None:
    return latest_file(PORTAL_OUTPUTS_DIR, pattern)


def _active_order_symbols() -> set[str]:
    cfg = alpaca_config()
    if not cfg.api_key or not cfg.secret_key:
        return set()
    try:
        orders = AlpacaPaperClient(cfg).list_orders(status="open", limit=500)
    except Exception:
        return set()
    return {str(row.get("symbol") or "").strip().upper() for row in orders if str(row.get("symbol") or "").strip()}


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
    holding_review_path = latest_file(ROOT / "data" / "trading" / "holding_period", "holding_review_*.csv")
    positions_path = Path(refreshed["positions_path"])
    plan = _read_csv(plan_path)
    candidate_pool = _read_csv(candidate_pool_path)
    results = _read_csv(result_path)
    holding_review = _read_csv(holding_review_path)
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
        holding_review,
        now=datetime.now(timezone.utc),
        signal_ttl_minutes=10,
        fallback_signal_time=fallback_signal_time,
    )

    journal_path = write_trade_journal(journal, stamp)
    pnl_path = write_pnl_summary(pnl, stamp)
    decision_path = write_position_decisions(decisions, stamp)
    closed_trades, closed_trades_path = write_reconstructed_closed_trades_attribution(root=ROOT, stamp=stamp)
    overnight_reprice = reprice_stale_overnight_close_orders()
    auto_close = execute_position_monitor_closes(
        positions,
        decisions,
        root=ROOT,
        active_order_symbols=_active_order_symbols(),
    )
    if int(auto_close.get("auto_close_attempted", 0) or 0) or int(overnight_reprice.get("overnight_reprice_attempted", 0) or 0):
        refreshed = refresh_order_tracking()
        positions_path = Path(refreshed["positions_path"])

    print(f"orders_tracked: {refreshed['orders_tracked']}")
    print(f"tracking_path: {refreshed['tracking_path']}")
    print(f"positions_path: {positions_path}")
    print(f"journal_rows: {len(journal)}")
    print(f"pnl_rows: {len(pnl)}")
    print(f"position_decisions: {len(decisions)}")
    print(f"journal_path: {journal_path}")
    print(f"pnl_path: {pnl_path}")
    print(f"decision_path: {decision_path}")
    print(f"closed_trades_rows: {len(closed_trades)}")
    print(f"closed_trades_path: {closed_trades_path}")
    print(f"cadence_reason: {cadence_reason}")
    if not decisions.empty:
        print(f"decision_counts: {decisions['decision'].value_counts().to_dict()}")
    print(f"auto_close_status: {auto_close.get('auto_close_status')}")
    print(f"auto_close_candidates: {auto_close.get('auto_close_candidates', 0)}")
    print(f"auto_close_attempted: {auto_close.get('auto_close_attempted', 0)}")
    print(f"auto_close_skipped_existing: {auto_close.get('auto_close_skipped_existing', 0)}")
    print(f"auto_close_submitted: {auto_close.get('auto_close_submitted', 0)}")
    print(f"auto_close_dry_run: {auto_close.get('auto_close_dry_run', 0)}")
    print(f"auto_close_rejected: {auto_close.get('auto_close_rejected', 0)}")
    print(f"auto_close_error: {auto_close.get('auto_close_error', 0)}")
    if auto_close.get("auto_close_reason"):
        print(f"auto_close_reason: {auto_close.get('auto_close_reason')}")
    if auto_close.get("auto_close_notes"):
        print(f"auto_close_notes: {auto_close.get('auto_close_notes')}")
    print(f"overnight_reprice_status: {overnight_reprice.get('overnight_reprice_status')}")
    print(f"overnight_reprice_candidates: {overnight_reprice.get('overnight_reprice_candidates', 0)}")
    print(f"overnight_reprice_attempted: {overnight_reprice.get('overnight_reprice_attempted', 0)}")
    print(f"overnight_reprice_canceled: {overnight_reprice.get('overnight_reprice_canceled', 0)}")
    print(f"overnight_reprice_submitted: {overnight_reprice.get('overnight_reprice_submitted', 0)}")
    print(f"overnight_reprice_skipped: {overnight_reprice.get('overnight_reprice_skipped', 0)}")
    print(f"overnight_reprice_error: {overnight_reprice.get('overnight_reprice_error', 0)}")
    if overnight_reprice.get("overnight_reprice_notes"):
        print(f"overnight_reprice_notes: {overnight_reprice.get('overnight_reprice_notes')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
