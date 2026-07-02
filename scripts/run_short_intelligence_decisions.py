from __future__ import annotations

import argparse
from pathlib import Path

from stockml.trading.short_intelligence_decision import latest_short_intelligence_inputs, write_short_intelligence_decisions


def main() -> int:
    parser = argparse.ArgumentParser(description="Run short intelligence decision diagnostics.")
    parser.add_argument("--start", default="", help="Reserved date filter for compatible invocation.")
    parser.add_argument("--end", default="", help="Reserved date filter for compatible invocation.")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    candidates, closed, sources = latest_short_intelligence_inputs()
    csv_path, md_path, decisions, summary = write_short_intelligence_decisions(candidates, closed, output_dir=args.output_dir)
    print(f"short_intelligence_status: {summary['recommendation']}")
    print(f"short_candidates: {summary['total']}")
    print(f"decision_counts: {summary['counts']}")
    print(f"inverse_watch_symbols: {','.join(summary['inverse_watch_symbols'])}")
    print(f"high_squeeze_symbols: {','.join(summary['high_squeeze_symbols'])}")
    print(f"sources: {sources}")
    print(f"decisions_path: {csv_path}")
    print(f"summary_path: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
