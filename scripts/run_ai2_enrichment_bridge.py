from __future__ import annotations

import argparse
from pathlib import Path

from stockml.ai2.bridge import run_ai2_enrichment_bridge
from stockml.ai2.candidate_enrichment import load_ai2_enrichment_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the StockML -> AI2 -> StockML candidate enrichment bridge.")
    parser.add_argument("--candidate-file", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--no-submit", action="store_true", help="Write the AI2 input and manifest without calling the AI2 API.")
    args = parser.parse_args()

    cfg = load_ai2_enrichment_config(Path(args.config) if args.config else None)
    result = run_ai2_enrichment_bridge(
        candidate_file=Path(args.candidate_file) if args.candidate_file else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        config=cfg,
        submit=not args.no_submit,
    )
    print(f"ai2_enrichment_bridge_status: {result.status}")
    print(f"candidate_path: {result.candidate_path}")
    print(f"input_path: {result.input_path}")
    print(f"response_path: {result.response_path}")
    print(f"merged_path: {result.merged_path}")
    print(f"manifest_path: {result.manifest_path}")
    print(f"rows: {result.rows}")
    print(f"ai2_rows: {result.ai2_rows}")
    print(f"ai2_auto_open_allowed: {result.ai2_auto_open_allowed}")
    if result.message:
        print(f"message: {result.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
