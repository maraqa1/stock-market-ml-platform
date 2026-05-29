from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, RAW_DIR, ensure_data_dirs, timestamp
from stockml.universe.clean_us_equity_universe import clean_universe_frame, tradable_only
from stockml.universe.fetch_nasdaq_symbols import fetch_us_equity_universe


def build_us_equity_universe(output_raw: Path = RAW_DIR, output_interim: Path = INTERIM_DIR) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()

    log("Fetching US equity symbol universe")
    universe = fetch_us_equity_universe()

    raw_path = output_raw / f"01_us_equity_universe_{stamp}.csv"
    universe.to_csv(raw_path, index=False)
    log(f"Wrote raw universe: {raw_path} ({len(universe):,} rows)")

    cleaned = clean_universe_frame(universe)
    clean_path = output_interim / f"02_us_universe_cleaned_{stamp}.csv"
    cleaned.to_csv(clean_path, index=False)
    log(f"Wrote cleaned universe: {clean_path} ({len(cleaned):,} rows)")

    tradable = tradable_only(universe)
    tradable_path = output_interim / f"02_us_tradable_universe_{stamp}.csv"
    tradable.to_csv(tradable_path, index=False)
    log(f"Wrote tradable universe: {tradable_path} ({len(tradable):,} rows)")

    summary_rows = [
        {"metric": "raw_rows", "value": len(universe)},
        {"metric": "cleaned_rows", "value": len(cleaned)},
        {"metric": "tradable_rows", "value": len(tradable)},
        {"metric": "excluded_rows", "value": int((~cleaned["is_tradable_common_stock_candidate"]).sum())},
        {"metric": "nasdaq_rows", "value": int((tradable["listing_exchange"] == "NASDAQ").sum())},
        {"metric": "nyse_rows", "value": int((tradable["listing_exchange"] == "NYSE").sum())},
        {"metric": "nyseamerican_rows", "value": int((tradable["listing_exchange"] == "NYSEAMERICAN").sum())},
    ]
    excluded = cleaned[~cleaned["is_tradable_common_stock_candidate"]].copy()
    for reason, count in excluded["exclude_reason"].value_counts().sort_index().items():
        summary_rows.append({"metric": f"excluded_{reason}", "value": int(count)})
    summary = pd.DataFrame(summary_rows)

    summary_path = output_interim / f"02_us_universe_summary_{stamp}.csv"
    summary.to_csv(summary_path, index=False)
    log(f"Wrote summary: {summary_path}")

    return {
        "raw_universe": raw_path,
        "cleaned_universe": clean_path,
        "tradable_universe": tradable_path,
        "summary": summary_path,
    }


def main() -> int:
    paths = build_us_equity_universe()
    log("Universe build complete")
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
