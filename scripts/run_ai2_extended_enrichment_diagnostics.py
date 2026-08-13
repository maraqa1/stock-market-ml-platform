from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.ai2_extended_enrichment import run_ai2_extended_enrichment_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Report optional AI2 extended enrichment coverage and shadow diagnostics.")
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("data/trading/diagnostics"))
    parser.add_argument("--max-quote-age-seconds", type=float, default=900.0)
    args = parser.parse_args()
    result = run_ai2_extended_enrichment_diagnostics(
        candidate_file=args.candidate_file,
        root=args.root,
        output_dir=args.output_dir,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )
    print(f"ai2_extended_enrichment_status: {result.status}")
    print(f"rows: {result.rows}")
    print(f"source_path: {result.source_path or ''}")
    for group, count in result.group_coverage.items():
        print(f"{group}_coverage_rows: {count}")
    print(f"detail_path: {result.detail_path}")
    print(f"summary_path: {result.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
