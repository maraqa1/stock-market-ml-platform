from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, ensure_data_dirs, timestamp
from stockml.reports.symbol_coverage_audit import AUDIT_COLUMNS, build_symbol_coverage_audit


SUMMARY_COLUMNS = ["stage", "reason", "symbols"]
ARTIFACT_ORDER = ["universe", "price", "validated", "metadata", "features", "gold", "model", "candidate_pool", "order_plan"]


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
    previous_mtime: float | None = None
    for name in ARTIFACT_ORDER:
        value = artifacts.get(name, "")
        path = Path(value) if value else None
        exists = bool(path and path.exists())
        modified = path.stat().st_mtime if exists else None
        stale_vs_upstream = bool(modified is not None and previous_mtime is not None and modified < previous_mtime)
        rows.append(
            {
                "artifact": name,
                "path": str(path or ""),
                "exists": exists,
                "size_bytes": int(path.stat().st_size) if exists else 0,
                "modified_utc": pd.to_datetime(modified, unit="s", utc=True).isoformat() if modified is not None else "",
                "stale_vs_upstream": stale_vs_upstream,
            }
        )
        if modified is not None:
            previous_mtime = max(previous_mtime or modified, modified)
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
