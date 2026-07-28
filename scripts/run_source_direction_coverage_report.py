#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.trading.forward_paper_reports import write_source_direction_coverage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-file", type=Path, default=None)
    args = parser.parse_args()
    result = write_source_direction_coverage(args.candidate_file)
    print("source_direction_coverage_status: ok")
    print("source_direction_coverage_path:", result.path)
    print("source_direction_coverage_summary_path:", result.summary_path)
    print("rows:", result.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
