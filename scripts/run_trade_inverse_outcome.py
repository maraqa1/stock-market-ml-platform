from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.trade_inverse_outcome import build_trade_inverse_outcome_from_latest, write_trade_inverse_outcome


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only actual-trade inverse outcome diagnostics.")
    parser.add_argument("--ledger-file", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    result = write_trade_inverse_outcome(build_trade_inverse_outcome_from_latest(args.ledger_file), output_dir=args.output_dir)
    summary = result.summary.iloc[0].to_dict() if not result.summary.empty else {}
    print("trade_inverse_outcome_status: ok" if int(summary.get("trade_count", 0) or 0) else "trade_inverse_outcome_status: insufficient_data")
    print(f"trade_count: {summary.get('trade_count', 0)}")
    print(f"actual_total_pnl: {summary.get('actual_total_pnl', 0)}")
    print(f"inverse_total_pnl_before_incremental_costs: {summary.get('inverse_total_pnl_before_incremental_costs', 0)}")
    print(f"recommended_action: {summary.get('recommended_action', '')}")
    print(f"report_path: {result.report_path}")
    print(f"summary_path: {result.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
