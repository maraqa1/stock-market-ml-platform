from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stockml.common.paths import PROJECT_ROOT
from stockml.diagnostics.short_inverse_shadow import write_short_inverse_shadow
from stockml.diagnostics.short_side_performance_guard import read_closed_trades, write_short_side_performance_guard


def _latest_execution_ranked() -> pd.DataFrame:
    files = sorted((PROJECT_ROOT / "data" / "portal_outputs").glob("execution_ranked_candidates_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return pd.DataFrame()
    return pd.read_csv(files[0], low_memory=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run short-side performance guard diagnostics.")
    parser.add_argument("--input", type=Path, required=True, help="Closed-trade attribution CSV.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "trading" / "diagnostics")
    args = parser.parse_args()

    closed = read_closed_trades(args.input)
    guard = write_short_side_performance_guard(closed, output_dir=args.output_dir)
    inverse_path = write_short_inverse_shadow(_latest_execution_ranked(), output_dir=args.output_dir)
    row = guard.frame.iloc[0]
    print(f"short_side_guard_status: {row['short_policy_decision']}")
    print(f"closed_trades: {row['closed_trades']}")
    print(f"closed_short_trades: {row['closed_short_trades']}")
    print(f"short_realised_pnl: {row['short_realised_pnl']}")
    print(f"short_win_rate: {row['short_win_rate']}")
    print(f"attribution_quality: {row['attribution_quality']}")
    print(f"warnings: {row['warnings']}")
    print(f"guard_path: {guard.csv_path}")
    print(f"guard_markdown_path: {guard.markdown_path}")
    print(f"inverse_shadow_path: {inverse_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
