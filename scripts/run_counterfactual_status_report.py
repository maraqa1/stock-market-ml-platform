#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.forward_paper_reports import write_counterfactual_status_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counterfactual-file", type=Path, default=None)
    args = parser.parse_args()
    result = write_counterfactual_status_report(args.counterfactual_file)
    print("counterfactual_status_report_status: ok")
    print("counterfactual_status_report_path:", result.path)
    print("counterfactual_status_report_summary_path:", result.summary_path)
    print("rows:", result.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
