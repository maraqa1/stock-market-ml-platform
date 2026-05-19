from __future__ import annotations

import argparse
from typing import Any, Dict

from stockml.common.logging_utils import log
from stockml.common.profiles import load_profile
from stockml.db.loaders import load_latest_outputs
from stockml.features.build_feature_panel import build_feature_panel
from stockml.gold.build_gold_dataset import build_gold_dataset
from stockml.metadata.build_metadata_enriched import build_metadata_enriched
from stockml.models.build_model_outputs import build_model_outputs
from stockml.prices.download_price_history import download_price_history
from stockml.prices.validate_price_history import build_price_quality_report
from stockml.sentiment.build_sentiment_panel import build_sentiment_panel
from stockml.universe.build_tradable_universe import build_us_equity_universe


def _limit(profile: Dict[str, Any], override_limit: int | None) -> int | None:
    return override_limit if override_limit is not None else profile.get("limit_tickers")


def run_profile(
    profile_name: str,
    override_limit: int | None = None,
    skip_sentiment: bool = False,
    write_database: bool = False,
    provider_name: str | None = None,
) -> None:
    profile = load_profile(profile_name)
    limit = _limit(profile, override_limit)
    exchange = profile.get("exchange")

    log(f"Starting profile pipeline: {profile_name}")
    log(f"Scope: exchange={exchange or 'ALL'} limit={limit or 'FULL'}")

    if profile.get("run_universe", True):
        build_us_equity_universe()

    if profile.get("run_price", True):
        download_price_history(
            start_date=str(profile.get("start_date", "2018-01-01")),
            batch_size=int(profile.get("batch_size", 75)),
            sleep_seconds=float(profile.get("sleep_seconds", 1.0)),
            limit=limit,
            exchange=exchange,
            provider_name=provider_name or profile.get("provider"),
        )
        build_price_quality_report()

    if profile.get("run_metadata", True):
        build_metadata_enriched(limit=limit, provider_name=provider_name or profile.get("provider"), exchange=exchange)

    if profile.get("run_features", True):
        build_feature_panel(limit_tickers=limit, exchange=exchange)

    if profile.get("run_sentiment", True) and not skip_sentiment:
        try:
            build_sentiment_panel(limit=limit, provider_name=profile.get("sentiment_provider"))
        except Exception as exc:
            log(f"Sentiment pipeline failed but profile will continue: {exc}")

    if profile.get("run_gold", True):
        build_gold_dataset(limit_tickers=limit, exchange=exchange)

    if profile.get("run_model", True):
        build_model_outputs(limit_tickers=profile.get("model_limit_tickers", limit), model_shards=int(profile.get("model_shards", 1) or 1))

    if write_database:
        counts = load_latest_outputs()
        log(f"Database load complete: {counts}")

    log(f"Profile pipeline complete: {profile_name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="nyse_full")
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--skip-sentiment", action="store_true")
    parser.add_argument("--write-database", action="store_true")
    parser.add_argument("--provider", default=None, help="Market data provider for price and metadata jobs: yahoo_legacy or eodhd.")
    args = parser.parse_args()
    run_profile(
        args.profile,
        override_limit=args.limit_tickers,
        skip_sentiment=args.skip_sentiment,
        write_database=args.write_database,
        provider_name=args.provider,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
