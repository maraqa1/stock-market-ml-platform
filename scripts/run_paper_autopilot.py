#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.paper_autopilot import action, context


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if command in {"mode", "set-mode"} and len(sys.argv) > 2:
        command = f"mode:{sys.argv[2]}"
    state = action(command)
    view = context()
    print(f"paper_autopilot_mode: {view['mode']}")
    print(f"paper_autopilot_status: {view['status']}")
    print(f"paper_autopilot_phase: {view['phase']}")
    print(f"open_orders: {view['open_orders']}")
    print(f"open_positions: {view['open_positions']}")
    print(f"termination_reason: {view['termination_reason']}")
    print(f"last_error: {view['last_error']}")
    print(f"state_path: {view['state_path']}")
    return 0 if state.get("status") != "stopped" or not state.get("last_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
