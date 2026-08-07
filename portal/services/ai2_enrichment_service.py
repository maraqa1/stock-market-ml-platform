from __future__ import annotations

from pathlib import Path

import pandas as pd

from portal.services.latest_file_reader import count_rows, file_status, latest_file, safe_read_csv
from stockml.ai2.candidate_enrichment import latest_ai2_enrichment_path, load_ai2_enrichment_config


def _status_counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if frame.empty or column not in frame.columns:
        return {}
    return {str(key): int(value) for key, value in frame[column].fillna("").value_counts().to_dict().items() if str(key)}


def ai2_enrichment_context(root: Path) -> dict:
    cfg = load_ai2_enrichment_config()
    candidate_file = latest_file(root, "portal_outputs", "execution_ranked_candidates_*.csv")
    ai2_input_file = latest_file(root, "ai2", "ai2_candidate_input_*.csv")
    ai2_file = latest_ai2_enrichment_path(root)
    merged_file = latest_file(root, "portal_outputs", "ai2_enriched_execution_ranked_candidates_*.csv")

    merged = safe_read_csv(merged_file, nrows=1000)
    ai2 = safe_read_csv(ai2_file, nrows=1000)
    decision_status = merged.get("ai2_decision_status", pd.Series(dtype=str)).fillna("").astype(str).str.lower() if not merged.empty else pd.Series(dtype=str)
    proceed = int(decision_status.eq("proceed").sum())
    allowed = int(merged.get("ai2_auto_open_allowed", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not merged.empty else 0
    rows = []
    if not merged.empty:
        display = merged.copy()
        display["__rank"] = pd.to_numeric(display.get("execution_rank"), errors="coerce")
        display["__raw"] = pd.to_numeric(display.get("raw_rank"), errors="coerce").fillna(999999)
        display = display.sort_values(
            ["ai2_auto_open_allowed", "__rank", "__raw", "symbol"],
            ascending=[False, True, True, True],
            na_position="last",
            kind="mergesort",
        )
        columns = [
            "symbol",
            "execution_rank",
            "final_execution_side",
            "status",
            "execution_domain",
            "ai2_decision",
            "ai2_decision_status",
            "ai2_price_check_status",
            "ai2_auto_open_allowed",
            "ai2_block_reason",
            "ai2_latest_intraday_price",
            "ai2_return_1d_pct",
            "ai2_return_5d_pct",
        ]
        rows = display[[column for column in columns if column in display.columns]].head(20).fillna("").to_dict("records")

    status = "disabled"
    if cfg.enabled and merged_file and allowed:
        status = "ready"
    elif cfg.enabled and merged_file:
        status = "waiting"
    elif merged_file:
        status = "diagnostic"

    return {
        "enabled": cfg.enabled,
        "status": status,
        "candidate_rows": count_rows(candidate_file),
        "ai2_input_rows": count_rows(ai2_input_file),
        "ai2_rows": len(ai2),
        "merged_rows": len(merged),
        "proceed_count": proceed,
        "auto_open_allowed_count": allowed,
        "decision_counts": _status_counts(merged, "ai2_decision_status"),
        "rows": rows,
        "files": [
            file_status(candidate_file, "Execution-ranked candidates"),
            file_status(ai2_input_file, "AI2 candidate input"),
            file_status(ai2_file, "AI2 enriched result"),
            file_status(merged_file, "AI2 merged candidates"),
        ],
    }

