#!/opt/jupyter-env/bin/python3
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.diagnostics.approved_market_cap_coverage import write_approved_market_cap_coverage_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose market_cap coverage for source-approved candidate names.")
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--metadata-file", type=Path, default=None)
    parser.add_argument("--validated-file", type=Path, default=None)
    parser.add_argument("--gold-file", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    result = write_approved_market_cap_coverage_report(
        candidate_file=args.candidate_file,
        metadata_file=args.metadata_file,
        validated_file=args.validated_file,
        gold_file=args.gold_file,
        output_dir=args.output_dir,
    )
    print("approved_market_cap_coverage_status:", result["status"])
    print("rows:", result["rows"])
    print("path:", result["path"])
    print("summary_path:", result["summary_path"])
    print("root_cause_counts:", result["root_cause_counts"])
    print("decision_counts:", result["decision_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
