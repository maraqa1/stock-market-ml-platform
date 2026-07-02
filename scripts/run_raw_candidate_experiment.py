from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.raw_candidate_experiment_attribution import build_raw_candidate_experiment_attribution
from stockml.experiments.raw_candidate_experiment import run_raw_candidate_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the paper-only raw candidate experiment lane.")
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Select and ledger candidates without broker submission.")
    parser.add_argument("--attribution-only", action="store_true")
    args = parser.parse_args()

    if args.attribution_only:
        report = build_raw_candidate_experiment_attribution()
        print(f"raw_candidate_experiment_attribution_status: {report['status']}")
        print(f"attribution_path: {report['csv_path']}")
        print(f"summary_path: {report['markdown_path']}")
        return 0

    result = run_raw_candidate_experiment(candidate_file=args.candidate_file, dry_run=args.dry_run)
    print(f"raw_candidate_experiment_status: {result.status}")
    print(f"selected: {result.selected}")
    print(f"submitted: {result.submitted}")
    print(f"skipped: {result.skipped}")
    print(f"events_path: {result.events_path}")
    print(f"trades_path: {result.trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
