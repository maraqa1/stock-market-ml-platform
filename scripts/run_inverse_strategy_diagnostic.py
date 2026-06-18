#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pandas as pd

from stockml.common.paths import ensure_data_dirs, timestamp
from stockml.diagnostics.inverse_strategy_diagnostic import build_inverse_strategy_report, write_inverse_summary
from stockml.diagnostics.ranking_polarity_diagnostic import build_ranking_polarity_report
from stockml.diagnostics.side_mapping_audit import build_side_mapping_audit_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build inverse strategy and ranking polarity diagnostics.")
    parser.add_argument("--stamp", default=None)
    parser.add_argument("--source-file", type=Path, default=None)
    parser.add_argument("--signal-file", type=Path, default=None)
    parser.add_argument("--gold-file", type=Path, default=None)
    args = parser.parse_args()
    ensure_data_dirs()
    stamp = args.stamp or timestamp()
    inverse = build_inverse_strategy_report(stamp, source_file=args.source_file)
    polarity = build_ranking_polarity_report(stamp, signal_file=args.signal_file, gold_file=args.gold_file)
    side = build_side_mapping_audit_report(stamp, order_file=args.source_file)
    inv_frame = pd.read_csv(inverse.path) if inverse.path.exists() else pd.DataFrame()
    pol_frame = pd.read_csv(polarity.path) if polarity.path.exists() else pd.DataFrame()
    summary = write_inverse_summary(stamp, inv_frame, pol_frame)
    for output in [inverse, polarity, side, summary]:
        print(f"{output.name}: {output.status} rows={output.rows} path={output.path}")
        if output.missing_inputs:
            print(f"{output.name}_missing_inputs: {','.join(output.missing_inputs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
