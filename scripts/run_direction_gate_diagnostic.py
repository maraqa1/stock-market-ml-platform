from __future__ import annotations

import argparse

from stockml.diagnostics.direction_gate_diagnostic import run_direction_gate_diagnostic


def main() -> int:
    parser = argparse.ArgumentParser(description="Run candidate direction-gate diagnostics.")
    parser.add_argument("--start", default="", help="Accepted for operator runbook compatibility; candidate pool is latest snapshot.")
    parser.add_argument("--end", default="", help="Accepted for operator runbook compatibility; candidate pool is latest snapshot.")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    result = run_direction_gate_diagnostic(output_dir=args.output_dir)
    print(f"direction_gate_diagnostic_status: {result['status']}")
    print(f"source_path: {result['source_path']}")
    print(f"csv_path: {result['csv_path']}")
    print(f"markdown_path: {result['markdown_path']}")
    print(f"candidates_analysed: {result['rows']}")
    print(f"direction_pass: {result['direction_pass']}")
    print(f"direction_block: {result['direction_block']}")
    print(f"direction_research_only: {result['direction_research_only']}")
    print(f"direction_inverse_watch: {result['direction_inverse_watch']}")
    print(f"direction_manual_review: {result['direction_manual_review']}")
    print(f"no_decision_blocked: {result['no_decision_blocked']}")
    print(f"short_blocked: {result['short_blocked']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
