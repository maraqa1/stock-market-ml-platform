from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.metadata.yahoo_metadata import METADATA_COLUMNS, build_metadata_quality, fetch_metadata_for_universe


def latest_validated_universe_file() -> Path:
    path = latest_file(INTERIM_DIR, "03_us_price_validated_universe_*.csv")
    if path is None:
        path = latest_file(INTERIM_DIR, "02_us_tradable_universe_*.csv")
    if path is None:
        raise FileNotFoundError("No validated or tradable universe file found. Run universe and price pipelines first.")
    return path


def _filter_universe_exchange(universe: pd.DataFrame, exchange: str | None) -> pd.DataFrame:
    if not exchange or "listing_exchange" not in universe.columns:
        return universe
    target = str(exchange).upper().strip()
    return universe[universe["listing_exchange"].astype(str).str.upper().str.strip().eq(target)].copy()


def build_metadata_enriched(
    limit: Optional[int] = None,
    sleep_seconds: float = 0.25,
    provider_name: str | None = None,
    fallback_provider_name: str | None = None,
    exchange: str | None = None,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    universe_path = latest_validated_universe_file()
    universe = pd.read_csv(universe_path, dtype=str)
    universe = _filter_universe_exchange(universe, exchange)
    metadata = fetch_metadata_for_universe(
        universe,
        limit=limit,
        sleep_seconds=sleep_seconds,
        provider_name=provider_name,
        fallback_provider_name=fallback_provider_name,
    )

    for col in METADATA_COLUMNS:
        if col not in metadata.columns:
            metadata[col] = pd.NA
    metadata = metadata[METADATA_COLUMNS]

    enriched_path = INTERIM_DIR / f"04_us_metadata_enriched_{stamp}.csv"
    metadata.to_csv(enriched_path, index=False)
    log(f"Wrote metadata enriched file: {enriched_path} ({len(metadata):,} rows)")

    quality = build_metadata_quality(metadata)
    quality_path = INTERIM_DIR / f"04_us_metadata_quality_{stamp}.csv"
    quality.to_csv(quality_path, index=False)
    log(f"Wrote metadata quality file: {quality_path} ({len(quality):,} rows)")

    return {"metadata_enriched": enriched_path, "metadata_quality": quality_path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--provider", default=None, help="Market data provider: yahoo_legacy or eodhd. Defaults to config/env.")
    parser.add_argument("--fallback-provider", default=None, help="Optional metadata fallback provider, e.g. yahoo_legacy.")
    parser.add_argument("--exchange", default=None, help="Optional listing exchange filter, e.g. NYSE")
    args = parser.parse_args()
    paths = build_metadata_enriched(
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        provider_name=args.provider,
        fallback_provider_name=args.fallback_provider,
        exchange=args.exchange,
    )
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
