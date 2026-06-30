from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.profitability_attribution import build_for_request, build_from_ledger_path, write_profitability_attribution


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only paper trade profitability attribution from the unified trade ledger.")
    parser.add_argument("--ledger", default="", help="Existing trade ledger CSV path. Defaults to latest ledger.")
    parser.add_argument("--date", default="", help="UTC date to build a fresh ledger first.")
    parser.add_argument("--start", default="", help="Inclusive UTC start timestamp/date for a fresh ledger.")
    parser.add_argument("--end", default="", help="Exclusive UTC end timestamp/date for a fresh ledger.")
    parser.add_argument("--output", default="data/trading/diagnostics", help="Output directory")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.date or (args.start and args.end):
        result = build_for_request(date_value=args.date, start=args.start, end=args.end)
    else:
        result = build_from_ledger_path(args.ledger or None)
    written = write_profitability_attribution(result, Path(args.output))
    print("profitability_attribution_status: ok")
    print(f"attribution_path: {Path(written.attribution_path).resolve()}")
    print(f"summary_path: {Path(written.summary_path).resolve()}")
    if written.ledger_path:
        print(f"ledger_path: {Path(written.ledger_path).resolve()}")
    for key, value in written.summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
