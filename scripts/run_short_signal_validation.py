from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.short_signal_validation import load_short_validation_inputs, run_short_signal_validation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dedicated short signal validation diagnostics.")
    parser.add_argument("--start", default="", help="Optional start date; reserved for date-filtered artifacts.")
    parser.add_argument("--end", default="", help="Optional end date; reserved for date-filtered artifacts.")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    candidates, closed, sources = load_short_validation_inputs()
    outputs = run_short_signal_validation(candidates, closed, output_dir=args.output_dir)
    summary = outputs.summary
    print(f"short_signal_validation_status: {summary['short_policy_recommendation']}")
    print(f"short_candidates: {summary['short_candidates']}")
    print(f"closed_short_trades: {summary['closed_short_trades']}")
    print(f"short_win_rate: {summary['short_win_rate']}")
    print(f"short_profit_factor: {summary['short_profit_factor']}")
    print(f"short_net_return_bps: {summary['short_net_return_bps']}")
    print(f"inverse_outperform_rate: {summary['inverse_outperform_rate']}")
    print(f"high_squeeze_count: {summary['high_squeeze_count']}")
    print(f"warnings: {summary['warnings']}")
    print(f"sources: {sources}")
    print(f"validation_path: {outputs.validation_path}")
    print(f"bucket_path: {outputs.bucket_path}")
    print(f"inverse_path: {outputs.inverse_path}")
    print(f"squeeze_path: {outputs.squeeze_path}")
    print(f"summary_path: {outputs.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
