from __future__ import annotations

import argparse
from pathlib import Path

from stockml.gold.enhanced_gold_v2 import build_enhanced_gold_v2, latest_gold_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Enhanced Gold v2 outputs alongside the legacy Gold dataset.")
    parser.add_argument("--gold-file", type=Path, default=None, help="Legacy 06_us_gold_ml_dataset CSV. Defaults to latest.")
    parser.add_argument("--latest", action="store_true", help="Use the latest legacy Gold dataset.")
    parser.add_argument("--candidate-limit", type=int, default=250)
    parser.add_argument("--chunk-size", type=int, default=200_000, help="Rows per streaming chunk.")
    args = parser.parse_args(argv)

    source = latest_gold_file() if args.latest or args.gold_file is None else args.gold_file
    outputs = build_enhanced_gold_v2(source, candidate_limit=args.candidate_limit, chunk_size=args.chunk_size)
    print(f"source_gold: {source}")
    print(f"decision_daily: {outputs.decision_daily}")
    print(f"candidates_latest: {outputs.candidates_latest}")
    print(f"feature_catalog: {outputs.feature_catalog}")
    print(f"data_quality_report: {outputs.data_quality_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
