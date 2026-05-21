from __future__ import annotations

import argparse
from typing import Any, Dict

from stockml.common.logging_utils import log
from stockml.common.profiles import load_profile
from stockml.common.exchange_scope import exchange_scope_label
from stockml.db.loaders import load_latest_outputs
from stockml.features.build_feature_panel import build_feature_panel
from stockml.gold.build_gold_dataset import build_gold_dataset
from stockml.metadata.build_metadata_enriched import build_metadata_enriched
from stockml.models.build_model_outputs import build_model_outputs
from stockml.pipeline.manifest import PipelineManifest
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
    exchange = profile.get("exchanges", profile.get("exchange"))
    effective_provider = provider_name or profile.get("provider")

    log(f"Starting profile pipeline: {profile_name}")
    log(f"Scope: exchange={exchange_scope_label(exchange)} limit={limit or 'FULL'}")
    manifest = PipelineManifest(profile_name)
    log(f"Pipeline manifest: {manifest.path}")

    current_stage = "start"
    try:
        if profile.get("run_universe", True):
            current_stage = "universe"
            universe_outputs = build_us_equity_universe()
            manifest.stage_ok("universe", universe_outputs if isinstance(universe_outputs, dict) else {})

        if profile.get("run_price", True):
            current_stage = "price"
            download_price_history(
                start_date=str(profile.get("start_date", "2018-01-01")),
                batch_size=int(profile.get("batch_size", 75)),
                sleep_seconds=float(profile.get("sleep_seconds", 1.0)),
                limit=limit,
                exchange=exchange,
                provider_name=effective_provider,
            )
            price_paths = build_price_quality_report(provider_name=effective_provider)
            manifest.stage_ok("price", price_paths)
        else:
            price_paths = {}

        if profile.get("run_metadata", True):
            current_stage = "metadata"
            metadata_paths = build_metadata_enriched(
                limit=limit,
                provider_name=effective_provider,
                fallback_provider_name=profile.get("metadata_fallback_provider"),
                exchange=exchange,
            )
            manifest.stage_ok("metadata", metadata_paths)
        else:
            metadata_paths = {}

        if profile.get("run_features", True):
            current_stage = "features"
            feature_paths = build_feature_panel(
                limit_tickers=limit,
                exchange=exchange,
                universe_file=price_paths.get("validated_universe"),
                metadata_file=metadata_paths.get("metadata_enriched"),
            )
            manifest.stage_ok("features", feature_paths)
        else:
            feature_paths = {}

        if profile.get("run_sentiment", True) and not skip_sentiment:
            current_stage = "sentiment"
            try:
                sentiment_paths = build_sentiment_panel(limit=limit, provider_name=profile.get("sentiment_provider"))
                manifest.stage_ok("sentiment", sentiment_paths)
            except Exception as exc:
                sentiment_paths = {}
                manifest.stage_ok("sentiment", {"warning": f"sentiment_failed_continue: {exc}"})
                log(f"Sentiment pipeline failed but profile will continue: {exc}")
        else:
            sentiment_paths = {}

        if profile.get("run_gold", True):
            current_stage = "gold"
            gold_paths = build_gold_dataset(
                limit_tickers=limit,
                exchange=exchange,
                feature_file=feature_paths.get("feature_panel"),
                sentiment_file=sentiment_paths.get("sentiment_panel"),
                skip_sentiment=bool(profile.get("skip_gold_sentiment", False)),
                shard_rows=profile.get("gold_shard_rows"),
            )
            manifest.stage_ok("gold", gold_paths)
        else:
            gold_paths = {}

        if profile.get("run_model", True):
            current_stage = "model"
            model_paths = build_model_outputs(
                gold_file=gold_paths.get("gold_dataset"),
                limit_tickers=profile.get("model_limit_tickers", limit),
                model_shards=int(profile.get("model_shards", 1) or 1),
                live_signal_mode=bool(profile.get("live_signal_mode", False)),
                baseline_only=bool(profile.get("baseline_only", False)),
            )
            manifest.stage_ok("model", model_paths)

        if write_database:
            current_stage = "database"
            counts = load_latest_outputs()
            manifest.stage_ok("database", counts)
            log(f"Database load complete: {counts}")

        manifest.complete()
        log(f"Profile pipeline complete: {profile_name}")
    except Exception as exc:
        manifest.stage_failed(current_stage, exc)
        log(f"Profile pipeline failed: {profile_name} manifest={manifest.path} error={exc}")
        raise


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
