#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.auto_trader import run_auto_trader


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-file", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Bypass the UTC auto-trade time window. Does not bypass submission flags.")
    args = parser.parse_args()
    result = run_auto_trader(signal_file=args.signal_file, force=args.force)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
