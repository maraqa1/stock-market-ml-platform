#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.paper_trader import refresh_order_tracking, run_paper_trading


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-file", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--track-only", action="store_true", help="Refresh order/position tracking from the latest result file without creating a new plan.")
    mode.add_argument("--plan-only", action="store_true", help="Write fresh candidate and order-plan artifacts without submitting broker orders.")
    parser.add_argument("--result-file", type=Path, default=None, help="Result CSV to refresh when --track-only is used.")
    args = parser.parse_args()
    result = refresh_order_tracking(args.result_file) if args.track_only else run_paper_trading(args.signal_file, plan_only=args.plan_only)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
