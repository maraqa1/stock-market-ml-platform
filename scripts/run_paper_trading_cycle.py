from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.agents.position_decision_engine import build_position_decisions, write_position_decisions
from stockml.trading.pnl_tracker import position_pnl_summary, write_pnl_summary
from stockml.trading.trade_journal import build_trade_journal, write_trade_journal


def _read_latest(pattern: str) -> pd.DataFrame:
    path = latest_file(PORTAL_OUTPUTS_DIR, pattern)
    return pd.read_csv(path, low_memory=False) if path and path.exists() else pd.DataFrame()


def _read_latest_holding_review() -> pd.DataFrame:
    path = latest_file(ROOT / "data" / "trading" / "holding_period", "holding_review_*.csv")
    return pd.read_csv(path, low_memory=False) if path and path.exists() else pd.DataFrame()


def main() -> int:
    ensure_data_dirs()
    stamp = timestamp()
    plan = _read_latest("08_alpaca_paper_order_plan_*.csv")
    results = _read_latest("08_alpaca_paper_order_results_*.csv")
    positions = _read_latest("08_alpaca_paper_positions_*.csv")
    holding_review = _read_latest_holding_review()
    journal = build_trade_journal(plan, results)
    pnl = position_pnl_summary(positions)
    decisions = build_position_decisions(positions, plan, results, holding_review=holding_review)
    journal_path = write_trade_journal(journal, stamp)
    pnl_path = write_pnl_summary(pnl, stamp)
    decision_path = write_position_decisions(decisions, stamp)
    print(f"journal_rows: {len(journal)}")
    print(f"pnl_rows: {len(pnl)}")
    print(f"position_decisions: {len(decisions)}")
    print(f"journal_path: {journal_path}")
    print(f"pnl_path: {pnl_path}")
    print(f"decision_path: {decision_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
