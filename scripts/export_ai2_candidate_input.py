from __future__ import annotations

import argparse

from stockml.ai2.candidate_input import write_latest_ai2_candidate_input


def main() -> int:
    parser = argparse.ArgumentParser(description="Export latest execution-ranked candidates for AI2 enrichment.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()

    source, output, rows = write_latest_ai2_candidate_input(output_dir=args.output_dir, limit=args.limit)
    if source is None or output is None:
        print("ai2_candidate_input_status: missing_data")
        print("missing_inputs: execution_ranked_candidates")
        return 0
    print("ai2_candidate_input_status: ok")
    print(f"source_path: {source}")
    print(f"output_path: {output}")
    print(f"rows: {rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

