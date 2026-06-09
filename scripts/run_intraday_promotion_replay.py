#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.reports.intraday_promotion_replay import build_intraday_promotion_replay


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only intraday promotion replay diagnostics.")
    parser.add_argument("--gold-file", type=Path, default=None)
    parser.add_argument("--stamp", default=None)
    args = parser.parse_args()
    outputs = build_intraday_promotion_replay(gold_file=args.gold_file, stamp=args.stamp)
    print("intraday_promotion_replay_status:", "missing_data" if outputs.missing_inputs else "ok")
    print("replay_rows:", outputs.replay_rows)
    print("summary_rows:", outputs.summary_rows)
    if outputs.missing_inputs:
        print("missing_inputs:", ",".join(outputs.missing_inputs))
    print("replay_path:", outputs.replay_path)
    print("summary_path:", outputs.summary_path)
    print("markdown_path:", outputs.markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
