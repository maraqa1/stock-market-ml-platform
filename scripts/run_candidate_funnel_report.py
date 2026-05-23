from __future__ import annotations

import argparse

from stockml.reports.candidate_funnel_report import build_candidate_funnel_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a raw-to-candidate funnel report for StockML symbols.")
    parser.add_argument("--provider", default=None, help="Optional provider filter for price history, e.g. eodhd.")
    parser.add_argument("--symbols", nargs="*", default=None, help="Optional symbols to focus the report.")
    args = parser.parse_args()

    result = build_candidate_funnel_report(provider_name=args.provider, symbols=args.symbols)
    print("candidate_funnel_status:", result["status"])
    print("symbols:", result["symbols"])
    print("audit_path:", result["audit_path"])
    print("summary_path:", result["summary_path"])
    print("artifact_path:", result["artifact_path"])
    print("top_drop_stages:")
    for row in result["top_drop_stages"]:
        print(f"  {row['stage']}: {row['reason']} = {row['symbols']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
