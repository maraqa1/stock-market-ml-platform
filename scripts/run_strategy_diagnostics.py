#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.common.paths import MODEL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.diagnostics.common import write_summary
from stockml.diagnostics.execution_attribution import build_execution_attribution_report
from stockml.diagnostics.fallback_attribution import build_fallback_attribution_report
from stockml.diagnostics.intraday_promotion_ablation import build_intraday_promotion_ablation_report
from stockml.diagnostics.long_short_edge import build_long_short_edge_report
from stockml.diagnostics.meta_label_impact import build_meta_label_impact_report
from stockml.diagnostics.position_management_attribution import build_position_management_report
from stockml.diagnostics.score_bucket_edge import build_score_bucket_edge_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only paper-trading strategy diagnostics.")
    parser.add_argument("--stamp", default=None, help="Optional output stamp for deterministic tests.")
    parser.add_argument("--signal-file", type=Path, default=None)
    parser.add_argument("--gold-file", type=Path, default=None)
    parser.add_argument("--result-file", type=Path, default=None)
    parser.add_argument("--tracking-file", type=Path, default=None)
    parser.add_argument("--event-file", type=Path, default=None)
    args = parser.parse_args()

    ensure_data_dirs()
    stamp = args.stamp or timestamp()
    outputs = [
        build_score_bucket_edge_report(stamp, signal_file=args.signal_file, gold_file=args.gold_file),
        build_long_short_edge_report(stamp, signal_file=args.signal_file, gold_file=args.gold_file),
        build_meta_label_impact_report(stamp, signal_file=args.signal_file, gold_file=args.gold_file),
        build_intraday_promotion_ablation_report(stamp, signal_file=args.signal_file, gold_file=args.gold_file),
        build_execution_attribution_report(stamp, result_file=args.result_file, tracking_file=args.tracking_file),
        build_position_management_report(stamp, event_file=args.event_file),
        build_fallback_attribution_report(stamp, signal_file=args.signal_file, gold_file=args.gold_file),
    ]
    summary = write_summary(outputs, MODEL_OUTPUTS_DIR / f"diagnostics_summary_{stamp}.md")
    for output in [*outputs, summary]:
        print(f"{output.name}: {output.status} rows={output.rows} path={output.path}")
        if output.missing_inputs:
            print(f"{output.name}_missing_inputs: {','.join(output.missing_inputs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

