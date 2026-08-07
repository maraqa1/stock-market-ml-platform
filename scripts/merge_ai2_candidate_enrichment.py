from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stockml.ai2.candidate_enrichment import (
    apply_ai2_enrichment,
    latest_ai2_enrichment_path,
    load_ai2_enrichment,
    load_ai2_enrichment_config,
    write_ai2_enriched_candidates,
)
from stockml.candidates.execution_ranker import latest_execution_ranked_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge AI2 enrichment onto execution-ranked candidates without submitting orders.")
    parser.add_argument("--candidate-file", default=None)
    parser.add_argument("--ai2-file", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    candidate_path = Path(args.candidate_file) if args.candidate_file else latest_execution_ranked_path()
    ai2_path = Path(args.ai2_file) if args.ai2_file else latest_ai2_enrichment_path()
    if candidate_path is None or not candidate_path.exists():
        print("ai2_enrichment_merge_status: missing_data")
        print("missing_inputs: execution_ranked_candidates")
        return 0
    if ai2_path is None or not ai2_path.exists():
        print("ai2_enrichment_merge_status: missing_data")
        print("missing_inputs: ai2_enrichment_file")
        return 0

    cfg = load_ai2_enrichment_config(args.config)
    candidates = pd.read_csv(candidate_path, low_memory=False)
    ai2 = load_ai2_enrichment(ai2_path)
    merged = apply_ai2_enrichment(candidates, ai2, config=cfg)
    output_path = write_ai2_enriched_candidates(candidates, ai2, output_dir=args.output_dir, config=cfg)

    print("ai2_enrichment_merge_status: ok")
    print(f"candidate_path: {candidate_path}")
    print(f"ai2_path: {ai2_path}")
    print(f"output_path: {output_path}")
    print(f"rows: {len(merged)}")
    print(f"ai2_matched_rows: {int(merged['ai2_decision_status'].fillna('').astype(str).ne('').sum())}")
    print(f"ai2_auto_open_allowed: {int(merged['ai2_auto_open_allowed'].fillna(False).astype(bool).sum())}")
    print(f"ai2_bridge_enabled: {cfg.enabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

