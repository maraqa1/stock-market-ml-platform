from __future__ import annotations

import argparse

from stockml.reports.ticker_lineage import build_ticker_lineage


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ticker-level lineage from source universe to trading candidate/order selection.")
    parser.add_argument("--symbols", nargs="*", default=None, help="Optional symbols to include in addition to latest candidates/orders.")
    args = parser.parse_args()

    result = build_ticker_lineage(symbols=args.symbols)
    print("ticker_lineage_status:", result["status"])
    print("rows:", result["rows"])
    print("symbols:", result["symbols"])
    print("warnings:", result["warnings"])
    print("path:", result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
