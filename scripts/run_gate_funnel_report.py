#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.forward_paper_reports import write_gate_funnel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--result-file", type=Path, default=None)
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    result = write_gate_funnel(args.candidate_file, args.result_file, run_date=args.run_date)
    print("gate_funnel_status: ok")
    print("gate_funnel_path:", result.path)
    print("gate_funnel_summary_path:", result.summary_path)
    print("rows:", result.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
