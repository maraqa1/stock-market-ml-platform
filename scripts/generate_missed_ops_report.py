from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT_PATH = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

import pandas as pd

from portal.services.latest_file_reader import latest_file, safe_read_csv
from stockml.common.paths import PROJECT_ROOT
from stockml.same_day.missed_ops import build_missed_opportunities, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate same-day missed opportunity report.")
    parser.add_argument("--session-date", default=date.today().isoformat())
    parser.add_argument("--bars-file")
    parser.add_argument("--universe-file")
    parser.add_argument("--signal-log-file")
    parser.add_argument("--traded-symbols-file")
    parser.add_argument("--move-threshold-pct", type=float, default=5.0)
    args = parser.parse_args(argv)

    root = PROJECT_ROOT
    session_date = date.fromisoformat(args.session_date)
    bars_file = Path(args.bars_file) if args.bars_file else root / "data" / "raw" / "intraday" / "5min_bars_store.csv"
    universe_file = Path(args.universe_file) if args.universe_file else latest_file(root, "interim", "02_us_tradable_universe_*.csv")
    signal_file = Path(args.signal_log_file) if args.signal_log_file else None
    traded_file = Path(args.traded_symbols_file) if args.traded_symbols_file else latest_file(root, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")

    bars = safe_read_csv(bars_file, nrows=None) if bars_file else pd.DataFrame()
    universe = safe_read_csv(universe_file, nrows=None) if universe_file else pd.DataFrame()
    signal_log = safe_read_csv(signal_file, nrows=None) if signal_file else pd.DataFrame()
    traded = safe_read_csv(traded_file, nrows=None) if traded_file else pd.DataFrame()
    traded_symbols = set()
    if not traded.empty and "symbol" in traded.columns:
        traded_symbols = {str(symbol).upper() for symbol in traded["symbol"].dropna()}

    report = build_missed_opportunities(
        session_date=session_date,
        intraday_bars=bars,
        universe=universe,
        signal_log=signal_log,
        traded_symbols=traded_symbols,
        move_threshold_pct=args.move_threshold_pct,
    )
    path = write_report(report, root / "reports" / "missed_opportunities")
    print(f"missed_opportunities_report: {path}")
    print(f"missed_opportunities_count: {len(report.rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
