from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.agents.position_decision_engine import build_position_decisions, write_position_decisions
from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, latest_file, timestamp


def _read_latest(pattern: str) -> tuple[pd.DataFrame, Path | None]:
    path = latest_file(PORTAL_OUTPUTS_DIR, pattern)
    return (pd.read_csv(path, low_memory=False), path) if path and path.exists() else (pd.DataFrame(), None)


def main() -> int:
    ensure_data_dirs()
    stamp = timestamp()
    plan, plan_path = _read_latest("08_alpaca_paper_order_plan_*.csv")
    results, _ = _read_latest("08_alpaca_paper_order_results_*.csv")
    positions, _ = _read_latest("08_alpaca_paper_positions_*.csv")
    fallback_signal_time = (
        datetime.fromtimestamp(plan_path.stat().st_mtime, tz=timezone.utc)
        if plan_path and plan_path.exists()
        else None
    )
    decisions = build_position_decisions(
        positions,
        plan,
        results,
        now=datetime.now(timezone.utc),
        signal_ttl_minutes=10,
        fallback_signal_time=fallback_signal_time,
    )
    decision_path = write_position_decisions(decisions, stamp)
    print(f"position_decisions: {len(decisions)}")
    print(f"decision_path: {decision_path}")
    if not decisions.empty:
        print(decisions["decision"].value_counts().to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
