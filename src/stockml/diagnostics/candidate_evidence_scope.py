from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.evidence_scope import enrich_candidate_evidence_scope, write_candidate_pool_splits
from stockml.candidates.execution_ranker import latest_execution_ranked_path
from stockml.common.paths import PROJECT_ROOT, timestamp


def _read(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _count_bool(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    return int(frame[column].fillna(False).astype(bool).sum())


def _side_summary(frame: pd.DataFrame, side_value: str) -> dict[str, Any]:
    if frame.empty:
        return {"count": 0, "avg_expected_bps": 0.0, "avg_hit_rate": 0.0, "avg_profit_factor": 0.0}
    side = frame.get("side", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    subset = frame[side.eq(side_value)].copy()
    return {
        "count": int(len(subset)),
        "avg_expected_bps": round(float(pd.to_numeric(subset.get("validated_expected_return_bps"), errors="coerce").mean()), 4) if len(subset) else 0.0,
        "avg_hit_rate": round(float(pd.to_numeric(subset.get("validated_hit_rate"), errors="coerce").mean()), 4) if len(subset) else 0.0,
        "avg_profit_factor": round(float(pd.to_numeric(subset.get("validated_profit_factor"), errors="coerce").mean()), 4) if len(subset) else 0.0,
    }


def _write_markdown(frame: pd.DataFrame, path: Path, *, source_path: Path | None, split_paths: dict[str, Path]) -> None:
    status_counts = frame.get("status", pd.Series(dtype=str)).fillna("").astype(str).value_counts().to_dict() if not frame.empty else {}
    scope_counts = frame.get("expected_return_scope", pd.Series(dtype=str)).fillna("unknown").astype(str).value_counts().to_dict() if not frame.empty else {}
    memory_counts = frame.get("ticker_direction_memory_status", pd.Series(dtype=str)).fillna("missing").astype(str).value_counts().to_dict() if not frame.empty else {}
    inverse_actionable = _count_bool(frame, "inverse_warning_actionable")
    inverse_present = int(frame.get("inverse_warning_status", pd.Series("", index=frame.index)).fillna("").astype(str).str.startswith("present").sum()) if not frame.empty else 0
    long_summary = _side_summary(frame, "buy")
    short_summary = _side_summary(frame, "sell")
    executable = int(frame.get("executable", pd.Series(False, index=frame.index)).fillna(False).astype(bool).sum()) if not frame.empty else 0
    research = int(frame.get("research_only", pd.Series(False, index=frame.index)).fillna(False).astype(bool).sum()) if not frame.empty else 0
    blocked = int(len(frame) - executable - research)
    non_ticker_exec = pd.DataFrame()
    allowed_missing_ticker = 0
    if not frame.empty:
        allowed_missing_ticker = int(
            frame.get("executable", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
            .where(frame.get("ticker_direction_memory_status", pd.Series("", index=frame.index)).isin(["missing", "insufficient_samples"]), False)
            .sum()
        )
        domain = frame.get("execution_domain", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
        scope = frame.get("expected_return_scope", pd.Series("unknown", index=frame.index)).fillna("unknown").astype(str).str.lower()
        non_ticker_exec = frame[domain.eq("execution_candidate") & ~scope.eq("ticker")].copy()
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Candidate Evidence Scope Diagnostic\n\n")
        handle.write(f"- Generated at UTC: `{datetime.now(timezone.utc).isoformat()}`\n")
        handle.write(f"- Source: `{source_path or ''}`\n")
        handle.write(f"- Rows: `{len(frame)}`\n")
        handle.write(f"- Executable count: `{executable}`\n")
        handle.write(f"- Research-only count: `{research}`\n")
        handle.write(f"- Blocked count: `{blocked}`\n")
        handle.write(f"- Inverse warnings present: `{inverse_present}`\n")
        handle.write(f"- Inverse warnings actionable: `{inverse_actionable}`\n")
        handle.write(f"- Candidates allowed without ticker-specific memory: `{allowed_missing_ticker}`\n\n")
        handle.write(f"- Execution candidates using non-ticker expected-return evidence: `{len(non_ticker_exec)}`\n\n")
        handle.write("## Split Files\n\n")
        for label, split_path in split_paths.items():
            handle.write(f"- {label}: `{split_path}`\n")
        handle.write("\n## Expected Return Scope Distribution\n\n")
        for key, value in scope_counts.items():
            handle.write(f"- {key}: `{value}`\n")
        handle.write("\n## Execution Candidates With Non-Ticker Evidence\n\n")
        if non_ticker_exec.empty:
            handle.write("- none\n")
        else:
            columns = [
                "symbol",
                "execution_rank",
                "expected_return_scope",
                "validated_expected_return_bps",
                "hit_rate_scope",
                "validated_hit_rate",
                "profit_factor_scope",
                "validated_profit_factor",
            ]
            available = [column for column in columns if column in non_ticker_exec.columns]
            for row in non_ticker_exec[available].fillna("").to_dict("records"):
                handle.write(
                    "- "
                    + ", ".join(f"{key}={value}" for key, value in row.items())
                    + "\n"
                )
        handle.write("\n## Ticker Direction Memory Coverage\n\n")
        for key, value in memory_counts.items():
            handle.write(f"- {key}: `{value}`\n")
        handle.write("\n## Long-Side Edge Summary\n\n")
        for key, value in long_summary.items():
            handle.write(f"- {key}: `{value}`\n")
        handle.write("\n## Short-Side Edge Summary\n\n")
        for key, value in short_summary.items():
            handle.write(f"- {key}: `{value}`\n")
        handle.write("\n## Status Distribution\n\n")
        for key, value in status_counts.items():
            handle.write(f"- {key}: `{value}`\n")


def run_candidate_evidence_scope(
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> dict[str, Any]:
    base = Path(root) if root else PROJECT_ROOT
    source_path = latest_execution_ranked_path(base)
    source = _read(source_path)
    run_stamp = stamp or timestamp()
    out_dir = Path(output_dir) if output_dir else base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = enrich_candidate_evidence_scope(source)
    csv_path = out_dir / f"candidate_evidence_scope_{run_stamp}.csv"
    md_path = out_dir / f"candidate_evidence_scope_{run_stamp}.md"
    split_paths = write_candidate_pool_splits(frame, output_dir=base / "data" / "trading" / "exports", stamp=run_stamp)
    frame.to_csv(csv_path, index=False)
    _write_markdown(frame, md_path, source_path=source_path, split_paths=split_paths)
    scope_distribution = frame.get("expected_return_scope", pd.Series(dtype=str)).fillna("unknown").astype(str).value_counts().to_dict() if not frame.empty else {}
    memory_distribution = frame.get("ticker_direction_memory_status", pd.Series(dtype=str)).fillna("missing").astype(str).value_counts().to_dict() if not frame.empty else {}
    return {
        "status": "ok" if source_path is not None and not frame.empty else "missing_data",
        "source_path": str(source_path or ""),
        "csv_path": str(csv_path),
        "markdown_path": str(md_path),
        "split_paths": {key: str(value) for key, value in split_paths.items()},
        "rows": len(frame),
        "executable_count": _count_bool(frame, "executable"),
        "research_only_count": _count_bool(frame, "research_only"),
        "blocked_count": int(len(frame) - _count_bool(frame, "executable") - _count_bool(frame, "research_only")),
        "expected_return_scope_distribution": scope_distribution,
        "ticker_memory_distribution": memory_distribution,
        "inverse_warnings_actionable": _count_bool(frame, "inverse_warning_actionable"),
        "execution_non_ticker_evidence_count": int(
            (
                frame.get("execution_domain", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower().eq("execution_candidate")
                & ~frame.get("expected_return_scope", pd.Series("unknown", index=frame.index)).fillna("unknown").astype(str).str.lower().eq("ticker")
            ).sum()
        ) if not frame.empty else 0,
    }
