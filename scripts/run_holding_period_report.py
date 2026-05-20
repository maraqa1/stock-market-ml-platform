from __future__ import annotations

import argparse
from pathlib import Path

from stockml.trading.holding_period import generate_holding_period_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-file", type=Path, default=None)
    parser.add_argument("--gold-file", type=Path, default=None)
    args = parser.parse_args()

    result = generate_holding_period_report(plan_file=args.plan_file, gold_file=args.gold_file)
    print("holding_period_status:", result["status"])
    print("rows:", result["rows"])
    print("path:", result["path"])
    print("plan_path:", result["plan_path"])
    print("gold_path:", result["gold_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
