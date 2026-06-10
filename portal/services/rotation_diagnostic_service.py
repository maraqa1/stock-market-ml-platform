from __future__ import annotations

from pathlib import Path
from typing import Any

from stockml.reports.held_vs_candidate import build_held_vs_candidate_diagnostic


def held_vs_candidate_context(root: Path, *, position_limit: int = 20, candidate_limit: int = 20) -> dict[str, Any]:
    result = build_held_vs_candidate_diagnostic(root=root, write=False, candidate_limit=candidate_limit)
    return {
        "status": result.get("status", ""),
        "generated_at": result.get("generated_at", ""),
        "summary": result.get("summary", {}),
        "held_positions": list(result.get("held_positions", []))[:position_limit],
        "available_candidates": list(result.get("available_candidates", []))[:candidate_limit],
        "position_rows": int(result.get("position_rows") or 0),
        "available_rows": int(result.get("available_rows") or 0),
        "warning_count": int(result.get("warning_count") or 0),
        "missing_inputs": list(result.get("missing_inputs", [])),
        "files": result.get("files", {}),
    }
