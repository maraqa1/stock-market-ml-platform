from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from portal.services.latest_file_reader import latest_file, project_root, safe_read_csv
from portal.services.trading_api_service import intraday_promotion_context
from portal.services.trading_service import trading_context
from stockml.trading.near_miss_analysis import OUTPUT_COLUMNS, near_miss_rows, write_near_miss_analysis


SEVERITY_ORDER = {"near_miss": 0, "moderate_gap": 1, "hard_fail": 2}


def _near_miss_output_dir(root: Path) -> Path:
    return project_root(root) / "data" / "trading" / "near_miss"


def _latest_near_miss_file(root: Path) -> Path | None:
    directory = _near_miss_output_dir(root)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("near_miss_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _input_frames(root: Path) -> list[pd.DataFrame]:
    candidate_pool = safe_read_csv(latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"), nrows=1000)
    plan = safe_read_csv(latest_file(root, "portal_outputs", "08_alpaca_paper_order_plan_*.csv"), nrows=1000)
    context = trading_context(root)
    rejected = pd.DataFrame(context.get("rejected_trimmed_rows") or [])
    intraday = pd.DataFrame((intraday_promotion_context(root).get("rows") or []))
    return [candidate_pool, plan, rejected, intraday]


def _summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "total_near_misses": 0,
            "hard_fails": 0,
            "near_misses_by_gate": {},
            "most_common_failed_gate": "None",
        }
    near = frame[frame["severity"] == "near_miss"]
    gate_counts = near["failed_gate_label"].value_counts().to_dict() if not near.empty else {}
    all_gate_counts = frame["failed_gate_label"].value_counts()
    return {
        "total_near_misses": int(len(near)),
        "hard_fails": int((frame["severity"] == "hard_fail").sum()),
        "near_misses_by_gate": gate_counts,
        "most_common_failed_gate": str(all_gate_counts.index[0]) if not all_gate_counts.empty else "None",
    }


def near_miss_context(root: Path | None = None, *, limit: int = 25) -> dict[str, Any]:
    resolved_root = project_root(root)
    frame = near_miss_rows(_input_frames(resolved_root))
    if not frame.empty:
        frame = frame.copy()
        frame["__severity_order"] = frame["severity"].map(SEVERITY_ORDER).fillna(9)
        frame["__distance_sort"] = pd.to_numeric(frame["distance_pct"], errors="coerce").fillna(999)
        frame = frame.sort_values(["__severity_order", "__distance_sort", "failed_gate", "symbol"]).drop(columns=["__severity_order", "__distance_sort"])
    path = write_near_miss_analysis(frame.reindex(columns=OUTPUT_COLUMNS), output_dir=_near_miss_output_dir(resolved_root))
    rows = frame.head(limit).fillna("").to_dict("records") if not frame.empty else []
    return {
        "source": "diagnostic_only",
        "path": str(path),
        "file_name": path.name,
        "rows": rows,
        "columns": OUTPUT_COLUMNS,
        "summary": _summary(frame),
    }


def latest_near_miss_context(root: Path | None = None, *, limit: int = 25) -> dict[str, Any]:
    resolved_root = project_root(root)
    path = _latest_near_miss_file(resolved_root)
    frame = safe_read_csv(path, nrows=1000)
    if path is None or frame.empty:
        return near_miss_context(resolved_root, limit=limit)
    return {
        "source": "diagnostic_only",
        "path": str(path),
        "file_name": path.name,
        "rows": frame.head(limit).fillna("").to_dict("records"),
        "columns": OUTPUT_COLUMNS,
        "summary": _summary(frame),
    }

