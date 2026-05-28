from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, PROCESSED_DIR, PROJECT_ROOT, latest_file
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
from stockml.reports.pipeline_quality_checks import build_pipeline_quality_report
from stockml.sentiment.build_sentiment_panel import build_sentiment_panel
from stockml.trading.holding_period import generate_holding_period_report
from stockml.trading.paper_trader import run_paper_trading
from stockml.universe.build_tradable_universe import build_us_equity_universe


def _symbol_set(path: Path | None, columns: list[str]) -> set[str]:
    if path is None or not path.exists():
        return set()
    try:
        header = pd.read_csv(path, nrows=0)
    except Exception:
        return set()
    column = next((name for name in columns if name in header.columns), None)
    if column is None:
        return set()
    symbols: set[str] = set()
    for chunk in pd.read_csv(path, usecols=[column], chunksize=200_000, dtype=str, low_memory=False):
        symbols.update(chunk[column].dropna().astype(str).str.upper().str.strip())
    symbols.discard("")
    return symbols


def _metadata_quality_gate(
    metadata_path: Path | None,
    validated_path: Path | None,
    *,
    min_validated_coverage: float = 0.75,
    min_market_cap_coverage: float = 0.70,
) -> None:
    if metadata_path is None or not metadata_path.exists():
        raise RuntimeError("metadata_quality_gate_failed: metadata artifact missing")
    if validated_path is None or not validated_path.exists():
        raise RuntimeError("metadata_quality_gate_failed: validated universe artifact missing")

    validated_symbols = _symbol_set(validated_path, ["yahoo_ticker", "ticker", "symbol"])
    metadata_symbols = _symbol_set(metadata_path, ["ticker", "symbol", "yahoo_ticker"])
    if not validated_symbols:
        raise RuntimeError("metadata_quality_gate_failed: validated universe has no symbols")

    total_rows = 0
    market_cap_rows = 0
    for chunk in pd.read_csv(metadata_path, usecols=lambda col: col in {"ticker", "market_cap"}, chunksize=200_000, low_memory=False):
        total_rows += len(chunk)
        if "market_cap" in chunk.columns:
            market_cap_rows += int(pd.to_numeric(chunk["market_cap"], errors="coerce").notna().sum())

    validated_coverage = len(metadata_symbols & validated_symbols) / max(len(validated_symbols), 1)
    market_cap_coverage = market_cap_rows / max(total_rows, 1)
    failures = []
    if validated_coverage < min_validated_coverage:
        failures.append(f"validated_coverage={validated_coverage:.4f}<required_{min_validated_coverage:.4f}")
    if market_cap_coverage < min_market_cap_coverage:
        failures.append(f"market_cap_coverage={market_cap_coverage:.4f}<required_{min_market_cap_coverage:.4f}")
    if failures:
        raise RuntimeError("metadata_quality_gate_failed: " + "; ".join(failures))


def _limit(profile: Dict[str, Any], override_limit: int | None) -> int | None:
    return override_limit if override_limit is not None else profile.get("limit_tickers")


def _publish_trading_artifacts(profile_name: str, profile: Dict[str, Any], limit: int | None) -> bool:
    if limit:
        return False
    return bool(profile.get("publish_trading_artifacts", profile_name == "us_full"))


def _required_latest(directory: Path, pattern: str, label: str) -> Path:
    path = latest_file(directory, pattern)
    if path is None:
        raise FileNotFoundError(f"No reusable {label} artifact found matching {pattern}")
    return path


def run_trading_day_readiness_gate() -> dict[str, Any]:
    quality = build_pipeline_quality_report(PROJECT_ROOT, profile_name="us_full")
    if quality.get("status") != "ok":
        raise RuntimeError(f"trading_day_readiness_failed: quality_gate_failed path={quality.get('path')}")

    plan = run_paper_trading(plan_only=True)
    if int(plan.get("candidate_pool_rows") or 0) <= 0 or int(plan.get("orders_planned") or 0) <= 0:
        raise RuntimeError(
            "trading_day_readiness_failed: empty_plan "
            f"candidate_pool_rows={plan.get('candidate_pool_rows')} orders_planned={plan.get('orders_planned')}"
        )

    holding = generate_holding_period_report(PROJECT_ROOT, plan_file=Path(plan["plan_path"]))
    if int(holding.get("review_rows") or 0) <= 0:
        raise RuntimeError(f"trading_day_readiness_failed: empty_holding_review plan_path={plan.get('plan_path')}")

    return {
        "pipeline_quality_path": str(quality.get("path") or ""),
        "candidate_pool_rows": int(plan.get("candidate_pool_rows") or 0),
        "orders_planned": int(plan.get("orders_planned") or 0),
        "orders_approved": int(plan.get("orders_approved") or 0),
        "orders_rejected": int(plan.get("orders_rejected") or 0),
        "plan_path": str(plan.get("plan_path") or ""),
        "result_path": str(plan.get("result_path") or ""),
        "holding_review_rows": int(holding.get("review_rows") or 0),
        "holding_review_passed": int(holding.get("review_passed") or 0),
        "holding_review_blocked": int(holding.get("review_blocked") or 0),
        "holding_review_path": str(holding.get("review_path") or ""),
    }


def run_profile(
    profile_name: str,
    override_limit: int | None = None,
    skip_sentiment: bool = False,
    write_database: bool = False,
    provider_name: str | None = None,
    reuse_existing_artifacts: bool = False,
    skip_price_download: bool = False,
) -> None:
    profile = load_profile(profile_name)
    limit = _limit(profile, override_limit)
    exchange = profile.get("exchanges", profile.get("exchange"))
    effective_provider = provider_name or profile.get("provider")
    publish_trading_artifacts = _publish_trading_artifacts(profile_name, profile, limit)

    log(f"Starting profile pipeline: {profile_name}")
    log(f"Scope: exchange={exchange_scope_label(exchange)} limit={limit or 'FULL'}")
    log(f"Publish canonical trading artifacts: {publish_trading_artifacts}")
    if reuse_existing_artifacts:
        log("Reuse existing artifacts: enabled (universe, price, metadata, and sentiment stages will not call external providers)")
    elif skip_price_download:
        log("Price download: skipped (price validation will reuse the existing price store)")
    manifest = PipelineManifest(profile_name)
    log(f"Pipeline manifest: {manifest.path}")

    current_stage = "start"
    try:
        if reuse_existing_artifacts:
            current_stage = "universe"
            universe_paths = {"tradable_universe": _required_latest(INTERIM_DIR, "02_us_tradable_universe_*.csv", "tradable universe")}
            manifest.stage_ok("universe", universe_paths)
        elif profile.get("run_universe", True):
            current_stage = "universe"
            universe_outputs = build_us_equity_universe()
            manifest.stage_ok("universe", universe_outputs if isinstance(universe_outputs, dict) else {})

        if reuse_existing_artifacts:
            current_stage = "price"
            price_paths = {"validated_universe": _required_latest(INTERIM_DIR, "03_us_price_validated_universe_*.csv", "validated universe")}
            manifest.stage_ok("price", price_paths)
        elif profile.get("run_price", True):
            current_stage = "price"
            if not skip_price_download:
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

        if reuse_existing_artifacts:
            current_stage = "metadata"
            metadata_paths = {"metadata_enriched": _required_latest(INTERIM_DIR, "04_us_metadata_enriched_*.csv", "metadata")}
            _metadata_quality_gate(
                metadata_paths.get("metadata_enriched"),
                price_paths.get("validated_universe"),
                min_validated_coverage=float(profile.get("min_metadata_validated_coverage", 0.75)),
                min_market_cap_coverage=float(profile.get("min_metadata_market_cap_coverage", 0.70)),
            )
            manifest.stage_ok("metadata", metadata_paths)
        elif profile.get("run_metadata", True):
            current_stage = "metadata"
            metadata_provider = profile.get("metadata_provider", effective_provider)
            metadata_paths = build_metadata_enriched(
                limit=limit,
                provider_name=metadata_provider,
                fallback_provider_name=profile.get("metadata_fallback_provider"),
                exchange=exchange,
                min_validated_coverage=float(profile.get("min_metadata_validated_coverage", 0.75)),
                min_market_cap_coverage=float(profile.get("min_metadata_market_cap_coverage", 0.70)),
            )
            _metadata_quality_gate(
                metadata_paths.get("metadata_enriched"),
                price_paths.get("validated_universe"),
                min_validated_coverage=float(profile.get("min_metadata_validated_coverage", 0.75)),
                min_market_cap_coverage=float(profile.get("min_metadata_market_cap_coverage", 0.70)),
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

        if reuse_existing_artifacts and profile.get("run_sentiment", True) and not skip_sentiment:
            current_stage = "sentiment"
            sentiment_path = latest_file(PROCESSED_DIR, "05_news_sentiment_panel_*.csv")
            sentiment_paths = {"sentiment_panel": sentiment_path} if sentiment_path else {}
            manifest.stage_ok("sentiment", sentiment_paths or {"warning": "no_reusable_sentiment_panel_found"})
        elif profile.get("run_sentiment", True) and not skip_sentiment:
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
                publish_latest=publish_trading_artifacts,
            )
            manifest.stage_ok("model", model_paths)

        if write_database:
            current_stage = "database"
            counts = load_latest_outputs()
            manifest.stage_ok("database", counts)
            log(f"Database load complete: {counts}")

        if profile.get("run_trading_day_readiness", False):
            current_stage = "trading_day_readiness"
            readiness = run_trading_day_readiness_gate()
            manifest.stage_ok("trading_day_readiness", readiness)
            log(f"Trading day readiness complete: {readiness}")

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
    parser.add_argument("--skip-price-download", action="store_true", help="Reuse the existing raw price store and rebuild price validation without downloading.")
    parser.add_argument(
        "--reuse-existing-artifacts",
        action="store_true",
        help="Do not call external providers; reuse latest universe, validated price, metadata, and sentiment artifacts.",
    )
    parser.add_argument("--write-database", action="store_true")
    parser.add_argument("--provider", default=None, help="Market data provider for price and metadata jobs: yahoo_legacy or eodhd.")
    args = parser.parse_args()
    run_profile(
        args.profile,
        override_limit=args.limit_tickers,
        skip_sentiment=args.skip_sentiment,
        write_database=args.write_database,
        provider_name=args.provider,
        reuse_existing_artifacts=args.reuse_existing_artifacts,
        skip_price_download=args.skip_price_download,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
