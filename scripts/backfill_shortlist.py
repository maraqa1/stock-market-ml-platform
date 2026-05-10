#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.common.paths import PORTAL_OUTPUTS_DIR
from stockml.trading.shortlist_snapshots import write_shortlist_snapshot_from_csv


def main() -> int:
    paths = sorted(
        PORTAL_OUTPUTS_DIR.glob("08_alpaca_paper_candidate_pool_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:30]
    total = 0
    for path in paths:
        rows = write_shortlist_snapshot_from_csv(path)
        total += rows
        print(f"{path.name}: {rows}")
    print(f"shortlist_snapshots_backfilled: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
