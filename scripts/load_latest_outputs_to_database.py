#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.db.loaders import load_latest_outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--skip-equity-universe", action="store_true")
    parser.add_argument("--skip-price-history", action="store_true")
    parser.add_argument("--skip-metadata", action="store_true")
    parser.add_argument("--skip-feature-panel", action="store_true")
    parser.add_argument("--skip-sentiment", action="store_true")
    parser.add_argument("--skip-gold-dataset", action="store_true")
    parser.add_argument("--skip-model-outputs", action="store_true")
    args = parser.parse_args()
    skip = set()
    if args.skip_equity_universe:
        skip.add("equity_universe")
    if args.skip_price_history:
        skip.add("price_history")
    if args.skip_metadata:
        skip.add("metadata_enriched")
    if args.skip_feature_panel:
        skip.add("feature_panel")
    if args.skip_sentiment:
        skip.add("sentiment_panel")
    if args.skip_gold_dataset:
        skip.add("gold_dataset")
    if args.skip_model_outputs:
        skip.add("model_outputs")
    counts = load_latest_outputs(args.database_url, skip=skip)
    for name, count in counts.items():
        print(f"{name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
