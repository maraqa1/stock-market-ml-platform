from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.autopilot.rotate import latest_promoted_candidates, write_rotation_recommendations
from stockml.common.paths import PORTAL_OUTPUTS_DIR


def _latest_positions() -> list[dict]:
    files = sorted(PORTAL_OUTPUTS_DIR.glob("08_alpaca_paper_positions_*.csv"), key=lambda path: path.stat().st_mtime)
    if not files:
        return []
    frame = pd.read_csv(files[-1], low_memory=False)
    if frame.empty:
        return []
    frame["position_id"] = frame.get("symbol", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper().map(lambda symbol: f"paper:{symbol}")
    return frame.fillna("").to_dict("records")


def main() -> None:
    promoted = latest_promoted_candidates()
    result = write_rotation_recommendations(promoted, _latest_positions())
    print("rotation_recommendation_status:", result.get("status"))
    print("promoted_candidates:", len(promoted))
    print("rotations_evaluated:", result.get("rotations_evaluated", 0))
    print("rotations_written:", result.get("rotations_written", 0))


if __name__ == "__main__":
    main()
