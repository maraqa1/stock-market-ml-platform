from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DIAGNOSTIC_FILES = {
    "broker_fill_reconciliation": ("broker_fill_reconciliation_*.csv", "Broker fill reconciliation", "data/trading/diagnostics"),
    "candidate_trade_attribution": ("candidate_trade_attribution_*.csv", "Candidate-to-trade attribution", "data/trading/diagnostics"),
    "missed_better_candidates": ("missed_better_candidates_*.csv", "Missed better candidates", "data/trading/diagnostics"),
    "position_management_outcomes": ("position_management_outcomes_*.csv", "Position management outcomes", "data/trading/diagnostics"),
    "ranking_polarity": ("ranking_polarity_diagnostic_*.csv", "Ranking polarity", "data/model_outputs/diagnostics"),
    "side_mapping_audit": ("side_mapping_audit_*.csv", "Side mapping audit", "data/model_outputs/diagnostics"),
}


def diagnostic_reports_context(root: Path, *, preview_rows: int = 50) -> dict[str, Any]:
    files = []
    previews: dict[str, list[dict[str, Any]]] = {}
    for key, (pattern, label, relative_dir) in DIAGNOSTIC_FILES.items():
        path = _latest(root / relative_dir, pattern)
        frame = _read_csv(path)
        files.append(
            {
                "kind": key,
                "label": label,
                "path": str(path) if path else "",
                "filename": path.name if path else "",
                "exists": bool(path),
                "rows": int(len(frame)),
                "csv_url": f"/reports/diagnostics/{key}.csv" if path else "",
                "status": _status(frame),
            }
        )
        previews[key] = _records(frame, preview_rows)
    return {
        "files": files,
        "previews": previews,
        "missing": all(not row["exists"] for row in files),
        "missing_message": "No attribution diagnostics were found. Run scripts/run_post_nightly_diagnostics.py after the nightly pipeline.",
    }


def diagnostic_report_csv(root: Path, kind: str) -> str | None:
    path = latest_diagnostic_report_file(root, kind)
    if not path:
        return None
    return path.read_text(encoding="utf-8")


def latest_diagnostic_report_file(root: Path, kind: str) -> Path | None:
    spec = DIAGNOSTIC_FILES.get(kind)
    if not spec:
        return None
    pattern, _label, relative_dir = spec
    return _latest(root / relative_dir, pattern)


def _latest(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: (path.stat().st_mtime, path.name))


def _read_csv(path: Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _status(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "missing"
    for column in ["status", "audit_flag", "strategy"]:
        if column in frame.columns:
            values = frame[column].fillna("").astype(str).str.lower()
            if values.isin(["missing_data", "insufficient_data"]).any():
                return "insufficient_data"
            if values.isin(["high", "long_mapped_to_sell", "short_mapped_to_buy", "no_decision_mapped_to_order"]).any():
                return "review"
    return "ok"


def _records(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    out = frame.head(limit).fillna("")
    return [{str(key): _json_value(value) for key, value in row.items()} for row in out.to_dict("records")]


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
