from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.source_approval_expansion_measurement import run_source_approval_expansion_measurement


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure source approval expansion buckets and counterfactual edge.")
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--counterfactual-file", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    result = run_source_approval_expansion_measurement(
        candidate_path=args.candidate_file,
        counterfactual_path=args.counterfactual_file,
        output_dir=args.output_dir,
    )
    print("source_approval_expansion_measurement_status: ok")
    print(f"ticket13_rows: {result.ticket13_rows}")
    for bucket, count in result.daily_bucket_counts.items():
        print(f"ticket13_{bucket}: {count}")
    print(f"ticket14_rows: {result.ticket14_rows}")
    print(f"ticket14_verdict: {result.edge_verdict}")
    for bucket, count in result.edge_n.items():
        print(f"ticket14_n_5d_{bucket}: {count}")
    print(f"materiality_confirmation: {result.materiality_confirmation}")
    print(f"ticket13_detail_path: {result.ticket13_detail_path}")
    print(f"ticket13_summary_path: {result.ticket13_summary_path}")
    print(f"ticket14_report_path: {result.ticket14_report_path}")
    print(f"ticket14_summary_path: {result.ticket14_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
