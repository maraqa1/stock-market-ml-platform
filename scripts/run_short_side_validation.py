from __future__ import annotations

from stockml.diagnostics.short_side_validation import run_short_side_validation


def main() -> int:
    output = run_short_side_validation()
    summary = output.summary
    print("short_side_validation_status: ok")
    print(f"short_candidate_count: {summary['short_candidate_count']}")
    print(f"source_approved_short_count: {summary['source_approved_short_count']}")
    print(f"short_win_rate: {summary['short_win_rate']}")
    print(f"short_average_return_bps: {summary['short_average_return_bps']}")
    print(f"short_expected_value_after_cost_bps: {summary['short_expected_value_after_cost_bps']}")
    print(f"short_profit_factor: {summary['short_profit_factor']}")
    print(f"short_execution_allowed: {summary['short_execution_allowed']}")
    print(f"decision: {summary['decision']}")
    print(f"csv_path: {output.csv_path}")
    print(f"markdown_path: {output.markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
