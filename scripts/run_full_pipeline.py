#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.common.logging_utils import log
from stockml.features.build_feature_panel import build_feature_panel
from stockml.gold.build_gold_dataset import build_gold_dataset
from stockml.metadata.build_metadata_enriched import build_metadata_enriched
from stockml.models.build_model_outputs import build_model_outputs
from stockml.prices.build_price_panel import main as run_price_main
from stockml.sentiment.build_sentiment_panel import build_sentiment_panel
from stockml.universe.build_tradable_universe import build_us_equity_universe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--exchange", default=None)
    parser.add_argument("--skip-price-download", action="store_true")
    parser.add_argument("--provider", default=None, help="Market data provider for price and metadata jobs: yahoo_legacy or eodhd.")
    parser.add_argument("--sentiment-provider", default=None, help="Sentiment provider: eodhd or legacy.")
    args = parser.parse_args()
    limit = args.limit_tickers or args.limit

    log("Starting full stockml pipeline")
    build_us_equity_universe()

    if args.skip_price_download:
        log("Skipping price download by request")
    else:
        original_argv = sys.argv
        price_args = []
        if limit:
            price_args.extend(["--limit", str(limit)])
        if args.exchange:
            price_args.extend(["--exchange", args.exchange])
        if args.provider:
            price_args.extend(["--provider", args.provider])
        sys.argv = [original_argv[0]] + price_args
        try:
            run_price_main()
        finally:
            sys.argv = original_argv

    build_metadata_enriched(limit=limit, provider_name=args.provider, exchange=args.exchange)
    build_feature_panel(limit_tickers=limit, exchange=args.exchange)

    try:
        build_sentiment_panel(limit=limit, provider_name=args.sentiment_provider)
    except Exception as exc:
        log(f"Sentiment pipeline failed but full pipeline will continue: {exc}")

    build_gold_dataset(limit_tickers=limit, exchange=args.exchange)
    build_model_outputs(limit_tickers=limit, publish_latest=limit is None)
    log("Full stockml pipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
