from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stockml.trading.activity_journal_export import (
    export_activity_journal,
    parse_utc_datetime,
    request_for_date,
    request_for_range,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the complete activity journal for a date or time range.")
    parser.add_argument("--date", help="UTC date to export, YYYY-MM-DD.")
    parser.add_argument("--start", help="Inclusive UTC start timestamp.")
    parser.add_argument("--end", help="Exclusive UTC end timestamp.")
    parser.add_argument("--source", action="append", default=[], help="Filter by event source. Repeatable.")
    parser.add_argument("--event-type", action="append", default=[], help="Filter by event type. Repeatable.")
    parser.add_argument("--symbol", default="", help="Filter by ticker symbol.")
    parser.add_argument("--output", default="data/trading/exports/", help="Output directory for CSV and metadata.")
    parser.add_argument("--batch-size", type=int, default=500, help="Internal DB page size.")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.date:
        request = request_for_date(
            date.fromisoformat(args.date),
            sources=args.source,
            event_types=args.event_type,
            symbol=args.symbol,
            batch_size=args.batch_size,
        )
    elif args.start and args.end:
        request = request_for_range(
            parse_utc_datetime(args.start),
            parse_utc_datetime(args.end),
            sources=args.source,
            event_types=args.event_type,
            symbol=args.symbol,
            batch_size=args.batch_size,
        )
    else:
        raise SystemExit("Provide --date or both --start and --end.")

    result = export_activity_journal(request, Path(args.output))
    print(f"activity_journal_export_status: ok")
    print(f"csv_path: {result.csv_path.resolve()}")
    print(f"metadata_path: {result.metadata_path.resolve()}")
    print(f"total_rows: {result.metadata['total_rows']}")
    print(f"was_truncated: {str(result.metadata['was_truncated']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
