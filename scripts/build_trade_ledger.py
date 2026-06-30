from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.trade_ledger_builder import build_trade_ledger, request_from_args, write_trade_ledger


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the unified paper trade ledger for a date or UTC time range.")
    parser.add_argument("--date", default="", help="UTC date, YYYY-MM-DD")
    parser.add_argument("--start", default="", help="Inclusive UTC start timestamp/date")
    parser.add_argument("--end", default="", help="Exclusive UTC end timestamp/date")
    parser.add_argument("--output", default="data/trading/diagnostics", help="Output directory")
    return parser.parse_args()


def main() -> int:
    args = _args()
    request = request_from_args(date_value=args.date, start=args.start, end=args.end)
    result = write_trade_ledger(build_trade_ledger(request), Path(args.output))
    print("trade_ledger_status: ok")
    print(f"ledger_path: {Path(result.ledger_path).resolve()}")
    print(f"unmatched_path: {Path(result.unmatched_path).resolve()}")
    print(f"summary_path: {Path(result.summary_path).resolve()}")
    for key, value in result.summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
