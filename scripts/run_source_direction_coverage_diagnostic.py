from __future__ import annotations

from stockml.diagnostics.source_direction_coverage import run_source_direction_coverage_diagnostic


def main() -> int:
    output = run_source_direction_coverage_diagnostic()
    print(f"source_direction_coverage_status: {output.status}")
    print(f"detail_rows: {output.detail_rows}")
    print(f"long_near_miss_count: {output.long_near_miss_count}")
    for reason, count in output.no_decision_reason_distribution.items():
        print(f"no_decision_reason:{reason}: {count}")
    print(f"detail_path: {output.detail_path}")
    print(f"summary_path: {output.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
