from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from portal.services.latest_file_reader import count_rows, latest_file, project_root, safe_read_csv


def _latest_forecast_file(root: Path | None = None) -> Path | None:
    return latest_file(root, "per_symbol_forecast", "per_symbol_forecast_*.csv")


def _top_counts(frame: pd.DataFrame, column: str, limit: int = 4) -> list[dict[str, Any]]:
    if frame.empty or column not in frame.columns:
        return []
    counts = frame[column].fillna("").astype(str)
    counts = counts[counts != ""].value_counts().head(limit)
    return [{"label": key, "rows": int(value)} for key, value in counts.items()]


def _display_rows(frame: pd.DataFrame, limit: int = 25) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    working = frame.copy()
    if "volatility_adjusted_score" in working.columns:
        working["_sort_score"] = pd.to_numeric(working["volatility_adjusted_score"], errors="coerce")
        working = working.sort_values("_sort_score", ascending=False, na_position="last")
    return working.head(limit).fillna("").to_dict("records")


def _mode_value(frame: pd.DataFrame, column: str, default: str = "") -> str:
    if frame.empty or column not in frame.columns:
        return default
    values = frame[column].dropna().astype(str)
    if values.empty:
        return default
    mode = values.mode()
    return mode.iat[0] if not mode.empty else default


def per_symbol_forecast_context(root: Path | None = None) -> dict[str, Any]:
    resolved = project_root(root)
    path = _latest_forecast_file(resolved)
    frame = safe_read_csv(path)
    row_count = count_rows(path)
    return {
        "file_name": path.name if path else "",
        "file_path": str(path or ""),
        "row_count": row_count,
        "rows": _display_rows(frame),
        "summary": {
            "total_rows": row_count,
            "top_sides": _top_counts(frame, "side"),
            "top_regimes": _top_counts(frame, "regime_label"),
            "top_reasons": _top_counts(frame, "forecast_reason"),
            "tier_c_status": _mode_value(frame, "tier_c_status", "uncalibrated"),
        },
    }
