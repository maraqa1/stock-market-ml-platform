from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.exchange_scope import filter_listing_exchange
from stockml.common.paths import INTERIM_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.metadata.yahoo_metadata import METADATA_COLUMNS, build_metadata_quality, fetch_metadata_for_universe


DEFAULT_MIN_VALIDATED_COVERAGE = 0.75
DEFAULT_MIN_MARKET_CAP_COVERAGE = 0.70


def latest_validated_universe_file() -> Path:
    path = latest_file(INTERIM_DIR, "03_us_price_validated_universe_*.csv")
    if path is None:
        path = latest_file(INTERIM_DIR, "02_us_tradable_universe_*.csv")
    if path is None:
        raise FileNotFoundError("No validated or tradable universe file found. Run universe and price pipelines first.")
    return path


def _filter_universe_exchange(universe: pd.DataFrame, exchange: object = None) -> pd.DataFrame:
    return filter_listing_exchange(universe, exchange=exchange)


def _scope_universe(universe: pd.DataFrame, *, exchange: object = None, limit: Optional[int] = None) -> pd.DataFrame:
    scoped = _filter_universe_exchange(universe, exchange)
    if limit:
        scoped = scoped.head(limit).copy()
    return scoped


def _symbol_set(frame: pd.DataFrame, columns: list[str]) -> set[str]:
    column = next((name for name in columns if name in frame.columns), None)
    if column is None:
        return set()
    values = frame[column].dropna().astype(str).str.upper().str.strip()
    return {value for value in values if value}


def metadata_quality_stats(metadata: pd.DataFrame, universe: pd.DataFrame) -> dict[str, float | int]:
    metadata_symbols = _symbol_set(metadata, ["ticker", "symbol", "yahoo_ticker"])
    universe_symbols = _symbol_set(universe, ["yahoo_ticker", "ticker", "symbol"])
    market_cap = pd.to_numeric(metadata.get("market_cap", pd.Series(index=metadata.index)), errors="coerce")
    return {
        "metadata_rows": int(len(metadata)),
        "universe_symbols": int(len(universe_symbols)),
        "validated_coverage": round(len(metadata_symbols & universe_symbols) / max(len(universe_symbols), 1), 4),
        "market_cap_coverage": round(float(market_cap.notna().mean()) if len(metadata) else 0.0, 4),
    }


def metadata_is_healthy(
    metadata: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    min_validated_coverage: float = DEFAULT_MIN_VALIDATED_COVERAGE,
    min_market_cap_coverage: float = DEFAULT_MIN_MARKET_CAP_COVERAGE,
) -> bool:
    stats = metadata_quality_stats(metadata, universe)
    return (
        float(stats["validated_coverage"]) >= min_validated_coverage
        and float(stats["market_cap_coverage"]) >= min_market_cap_coverage
    )


def _latest_healthy_metadata(
    universe: pd.DataFrame,
    *,
    min_validated_coverage: float,
    min_market_cap_coverage: float,
) -> tuple[Path, pd.DataFrame, dict[str, float | int]] | None:
    files = sorted(INTERIM_DIR.glob("04_us_metadata_enriched_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        for col in METADATA_COLUMNS:
            if col not in frame.columns:
                frame[col] = pd.NA
        frame = frame[METADATA_COLUMNS]
        stats = metadata_quality_stats(frame, universe)
        if (
            float(stats["validated_coverage"]) >= min_validated_coverage
            and float(stats["market_cap_coverage"]) >= min_market_cap_coverage
        ):
            return path, frame, stats
    return None


def build_metadata_enriched(
    limit: Optional[int] = None,
    sleep_seconds: float = 0.25,
    provider_name: str | None = None,
    fallback_provider_name: str | None = None,
    exchange: object = None,
    min_validated_coverage: float = DEFAULT_MIN_VALIDATED_COVERAGE,
    min_market_cap_coverage: float = DEFAULT_MIN_MARKET_CAP_COVERAGE,
    reuse_last_good_on_failure: bool = True,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    universe_path = latest_validated_universe_file()
    universe = pd.read_csv(universe_path, dtype=str)
    universe = _scope_universe(universe, exchange=exchange, limit=limit)
    metadata = fetch_metadata_for_universe(
        universe,
        limit=None,
        sleep_seconds=sleep_seconds,
        provider_name=provider_name,
        fallback_provider_name=fallback_provider_name,
    )

    for col in METADATA_COLUMNS:
        if col not in metadata.columns:
            metadata[col] = pd.NA
    metadata = metadata[METADATA_COLUMNS]
    stats = metadata_quality_stats(metadata, universe)

    source = "fresh"
    reused_from = ""
    if not metadata_is_healthy(
        metadata,
        universe,
        min_validated_coverage=min_validated_coverage,
        min_market_cap_coverage=min_market_cap_coverage,
    ):
        message = (
            "metadata_quality_gate_failed_before_publish: "
            f"validated_coverage={stats['validated_coverage']} required>={min_validated_coverage}; "
            f"market_cap_coverage={stats['market_cap_coverage']} required>={min_market_cap_coverage}"
        )
        if not reuse_last_good_on_failure:
            raise RuntimeError(message)
        fallback = _latest_healthy_metadata(
            universe,
            min_validated_coverage=min_validated_coverage,
            min_market_cap_coverage=min_market_cap_coverage,
        )
        if fallback is None:
            raise RuntimeError(f"{message}; no previous healthy metadata snapshot found")
        reused_from, metadata, stats = str(fallback[0]), fallback[1], fallback[2]
        source = "reused_last_good"
        log(f"{message}; reusing last healthy metadata snapshot: {reused_from}")

    enriched_path = INTERIM_DIR / f"04_us_metadata_enriched_{stamp}.csv"
    metadata.to_csv(enriched_path, index=False)
    log(
        f"Wrote metadata enriched file: {enriched_path} ({len(metadata):,} rows) "
        f"source={source} validated_coverage={stats['validated_coverage']} "
        f"market_cap_coverage={stats['market_cap_coverage']}"
    )

    quality = build_metadata_quality(metadata)
    quality["metadata_build_source"] = source
    quality["metadata_reused_from"] = reused_from
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
    parser.add_argument("--min-validated-coverage", type=float, default=DEFAULT_MIN_VALIDATED_COVERAGE)
    parser.add_argument("--min-market-cap-coverage", type=float, default=DEFAULT_MIN_MARKET_CAP_COVERAGE)
    parser.add_argument("--no-reuse-last-good", action="store_true", help="Fail instead of publishing the latest healthy metadata snapshot when fresh metadata is unhealthy.")
    args = parser.parse_args()
    paths = build_metadata_enriched(
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        provider_name=args.provider,
        fallback_provider_name=args.fallback_provider,
        exchange=args.exchange,
        min_validated_coverage=args.min_validated_coverage,
        min_market_cap_coverage=args.min_market_cap_coverage,
        reuse_last_good_on_failure=not args.no_reuse_last_good,
    )
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
