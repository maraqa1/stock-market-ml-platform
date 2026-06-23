from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, timestamp


def write_execution_report(rows: list[dict], prefix: str = "08_paper_execution_report") -> Path:
    ensure_data_dirs()
    path = PORTAL_OUTPUTS_DIR / f"{prefix}_{timestamp()}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def attach_execution_lineage(rows: list[dict], lineage_by_symbol: dict[str, dict]) -> list[dict]:
    enriched: list[dict] = []
    for row in rows:
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        lineage = lineage_by_symbol.get(symbol, {})
        enriched.append({**row, **{key: value for key, value in lineage.items() if value not in (None, "")}})
    return enriched
