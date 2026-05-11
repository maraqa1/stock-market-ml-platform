from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from portal.services.latest_file_reader import latest_file, safe_read_csv
from stockml.agents.candidate_evaluation_engine import evaluate_candidates, write_candidate_evaluations


def main() -> int:
    candidate_path = latest_file(ROOT, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    positions_path = latest_file(ROOT, "portal_outputs", "08_alpaca_paper_positions_*.csv")
    candidates = safe_read_csv(candidate_path, nrows=100)
    positions = safe_read_csv(positions_path, nrows=1000)
    evaluations = evaluate_candidates(candidates, positions)
    path = write_candidate_evaluations(evaluations)
    counts = evaluations["decision"].value_counts().to_dict() if "decision" in evaluations.columns else {}
    print(f"candidate_evaluations: {len(evaluations)}")
    print(f"decision_counts: {counts}")
    print(f"evaluation_path: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
