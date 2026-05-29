from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stockml.same_day.training import run_same_day_edge_measurement


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure retrospective same-day momentum edge.")
    parser.add_argument("--bars-file", type=Path, required=True, help="Historical 5-minute bars CSV.")
    parser.add_argument("--report-dir", type=Path, default=None, help="Output directory for markdown report.")
    parser.add_argument("--stamp", default=None, help="Optional report filename stamp.")
    args = parser.parse_args(argv)

    if not args.bars_file.exists():
        parser.error(
            f"bars file not found: {args.bars_file}. "
            "Generate one first, e.g. "
            "PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/download_intraday_history.py "
            "--start-date YYYY-MM-DD --end-date YYYY-MM-DD --provider eodhd"
        )
    bars = pd.read_csv(args.bars_file, low_memory=False)
    path = run_same_day_edge_measurement(bars, report_dir=args.report_dir, stamp=args.stamp)
    print(f"same_day_edge_report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
