from __future__ import annotations

import argparse

from stockml.reports.pipeline_quality_checks import PipelineQualityThresholds, build_pipeline_quality_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate StockML universe, metadata, and Gold dataset quality.")
    parser.add_argument("--min-universe-rows", type=int, default=PipelineQualityThresholds.min_universe_rows)
    parser.add_argument("--min-validated-rows", type=int, default=PipelineQualityThresholds.min_validated_rows)
    parser.add_argument("--min-validated-universe-coverage", type=float, default=PipelineQualityThresholds.min_validated_universe_coverage)
    parser.add_argument("--min-metadata-validated-coverage", type=float, default=PipelineQualityThresholds.min_metadata_validated_coverage)
    parser.add_argument("--min-metadata-market-cap-coverage", type=float, default=PipelineQualityThresholds.min_metadata_market_cap_coverage)
    parser.add_argument("--min-gold-rows", type=int, default=PipelineQualityThresholds.min_gold_rows)
    parser.add_argument("--min-gold-validated-coverage", type=float, default=PipelineQualityThresholds.min_gold_validated_coverage)
    parser.add_argument("--max-gold-missing-market-cap-rate", type=float, default=PipelineQualityThresholds.max_gold_missing_market_cap_rate)
    parser.add_argument("--max-gold-duplicate-key-rate", type=float, default=PipelineQualityThresholds.max_gold_duplicate_key_rate)
    args = parser.parse_args()

    thresholds = PipelineQualityThresholds(
        min_universe_rows=args.min_universe_rows,
        min_validated_rows=args.min_validated_rows,
        min_validated_universe_coverage=args.min_validated_universe_coverage,
        min_metadata_validated_coverage=args.min_metadata_validated_coverage,
        min_metadata_market_cap_coverage=args.min_metadata_market_cap_coverage,
        min_gold_rows=args.min_gold_rows,
        min_gold_validated_coverage=args.min_gold_validated_coverage,
        max_gold_missing_market_cap_rate=args.max_gold_missing_market_cap_rate,
        max_gold_duplicate_key_rate=args.max_gold_duplicate_key_rate,
    )
    result = build_pipeline_quality_report(thresholds=thresholds)
    print("pipeline_quality_status:", result["status"])
    print("checks:", result["checks"])
    print("failed_checks:", result["failed_checks"])
    print("path:", result["path"])
    if result["failures"]:
        print("failures:")
        for row in result["failures"]:
            print(f"  {row['check']}: observed={row['observed']} threshold={row['threshold']} message={row['message']}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

