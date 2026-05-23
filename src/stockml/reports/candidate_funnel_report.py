from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, ensure_data_dirs, timestamp
from stockml.reports.symbol_coverage_audit import AUDIT_COLUMNS, build_symbol_coverage_audit


SUMMARY_COLUMNS = ["stage", "reason", "symbols"]


def _read_audit(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    for column in AUDIT_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[AUDIT_COLUMNS].copy()


def _stage_summary(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    summary = (
        audit.groupby(["drop_stage", "drop_reason"], dropna=False)
        .size()
        .reset_index(name="symbols")
        .rename(columns={"drop_stage": "stage", "drop_reason": "reason"})
        .sort_values(["symbols", "stage", "reason"], ascending=[False, True, True])
    )
    return summary[SUMMARY_COLUMNS]


def _artifact_rows(artifacts: dict[str, str]) -> pd.DataFrame:
    rows = []
    for name, value in artifacts.items():
        path = Path(value) if value else None
        rows.append(
            {
                "artifact": name,
                "path": str(path or ""),
                "exists": bool(path and path.exists()),
                "size_bytes": int(path.stat().st_size) if path and path.exists() else 0,
                "modified_utc": pd.to_datetime(path.stat().st_mtime, unit="s", utc=True).isoformat() if path and path.exists() else "",
            }
        )
    return pd.DataFrame(rows)


def build_candidate_funnel_report(
    root: Path | None = None,
    provider_name: str | None = None,
    symbols: Iterable[str] | None = None,
    stamp: str | None = None,
) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    ensure_data_dirs()
    run_stamp = stamp or timestamp()
    audit_result = build_symbol_coverage_audit(
        root=base,
        symbols=symbols,
        provider_name=provider_name,
        stamp=run_stamp,
    )
    audit_path = Path(str(audit_result["path"]))
    audit = _read_audit(audit_path)
    summary = _stage_summary(audit)
    artifacts = _artifact_rows(dict(audit_result.get("artifacts", {})))

    interim = base / "data" / "interim"
    summary_path = interim / f"00_candidate_funnel_summary_{run_stamp}.csv"
    artifact_path = interim / f"00_candidate_funnel_artifacts_{run_stamp}.csv"
    summary.to_csv(summary_path, index=False)
    artifacts.to_csv(artifact_path, index=False)

    return {
        "status": "ok",
        "symbols": int(len(audit)),
        "audit_path": str(audit_path),
        "summary_path": str(summary_path),
        "artifact_path": str(artifact_path),
        "top_drop_stages": summary.head(20).to_dict("records"),
    }
