#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.paper_autopilot import context, tick


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Paper Autopilot tick.")
    parser.add_argument("--dry-run", action="store_true", help="Force submit_orders=false for this process.")
    args = parser.parse_args()

    if args.dry_run:
        os.environ["STOCKML_ALPACA_SUBMIT_ORDERS"] = "false"

    state = tick()
    view = context()
    print(f"paper_autopilot_tick_status: {state.get('status')}")
    print(f"paper_autopilot_phase: {state.get('phase')}")
    print(f"dry_run: {bool(args.dry_run)}")
    print(f"autopilot_open_attempted: {state.get('autopilot_open_attempted', 0)}")
    print(f"autopilot_open_submitted: {state.get('autopilot_open_submitted', 0)}")
    print(f"autopilot_open_blocked: {state.get('autopilot_open_blocked', 0)}")
    print(f"autopilot_open_notes: {state.get('autopilot_open_notes', '')}")
    print(f"open_orders: {view.get('open_orders')}")
    print(f"open_positions: {view.get('open_positions')}")
    print(f"last_error: {view.get('last_error')}")
    return 0 if not state.get("last_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
