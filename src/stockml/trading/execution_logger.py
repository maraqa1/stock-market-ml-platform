from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, timestamp


def write_execution_report(rows: list[dict], prefix: str = "08_paper_execution_report") -> Path:
    ensure_data_dirs()
    path = PORTAL_OUTPUTS_DIR / f"{prefix}_{timestamp()}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path
