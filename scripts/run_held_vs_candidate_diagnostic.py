#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.reports.held_vs_candidate import write_held_vs_candidate_diagnostic


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only held-position versus candidate-pool diagnostics.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stamp", default=None)
    parser.add_argument("--candidate-limit", type=int, default=50)
    args = parser.parse_args()
    outputs = write_held_vs_candidate_diagnostic(root=args.root, stamp=args.stamp, candidate_limit=args.candidate_limit)
    print("held_vs_candidate_status:", "missing_data" if outputs.missing_inputs else "ok")
    print("position_rows:", outputs.position_rows)
    print("available_rows:", outputs.available_rows)
    print("warning_count:", outputs.warning_count)
    if outputs.missing_inputs:
        print("missing_inputs:", ",".join(outputs.missing_inputs))
    print("positions_path:", outputs.positions_path)
    print("available_path:", outputs.available_path)
    print("summary_path:", outputs.summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
