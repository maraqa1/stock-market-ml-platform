#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.paper_trader import run_paper_trading


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-file", type=Path, default=None)
    args = parser.parse_args()
    result = run_paper_trading(args.signal_file)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

