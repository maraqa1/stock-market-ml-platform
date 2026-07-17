from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stockml.common.paths import PORTAL_OUTPUTS_DIR, latest_file
from stockml.trading.counterfactual_log import write_counterfactual_candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-pool", default=None)
    parser.add_argument("--order-plan", default=None)
    parser.add_argument("--cycle-id", default="")
    parser.add_argument("--pipeline-run-id", default="")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    candidate_path = Path(args.candidate_pool) if args.candidate_pool else latest_file(PORTAL_OUTPUTS_DIR, "08_alpaca_paper_candidate_pool_*.csv")
    plan_path = Path(args.order_plan) if args.order_plan else latest_file(PORTAL_OUTPUTS_DIR, "08_alpaca_paper_order_plan_*.csv")
    candidates = pd.read_csv(candidate_path, low_memory=False) if candidate_path and candidate_path.exists() else pd.DataFrame()
    plan = pd.read_csv(plan_path, low_memory=False) if plan_path and plan_path.exists() else pd.DataFrame()
    result = write_counterfactual_candidates(
        candidates,
        plan=plan,
        cycle_id=args.cycle_id,
        pipeline_run_id=args.pipeline_run_id,
        candidate_source_path=str(candidate_path or ""),
        order_plan_path=str(plan_path or ""),
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print("counterfactual_candidate_log_status: ok")
    print("counterfactual_candidate_log_path:", result.path)
    print("rows:", result.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
