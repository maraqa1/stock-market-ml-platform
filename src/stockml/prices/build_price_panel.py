from __future__ import annotations

import argparse

from stockml.common.logging_utils import log
from stockml.prices.download_price_history import download_price_history
from stockml.prices.validate_price_history import build_price_quality_report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", default="2018-01-01")
    p.add_argument("--batch-size", type=int, default=75)
    p.add_argument("--sleep-seconds", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--force-full", action="store_true")
    args = p.parse_args()

    log("Starting price-history pipeline")
    download_price_history(
        start_date=args.start_date,
        batch_size=args.batch_size,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
        force_full=args.force_full,
    )
    build_price_quality_report()
    log("Price-history pipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
