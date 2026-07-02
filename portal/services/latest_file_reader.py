from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from stockml.decisions.reason_formatter import format_reasons


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIRS = {
    "raw": "data/raw",
    "interim": "data/interim",
    "processed": "data/processed",
    "gold": "data/gold",
    "model_outputs": "data/model_outputs",
    "portal_outputs": "data/portal_outputs",
    "paper_trade_journal": "data/trading/paper_trade_journal",
    "paper_pnl": "data/trading/paper_pnl",
    "paper_positions": "data/trading/paper_positions",
    "agent_decisions": "data/trading/agent_decisions",
    "candidate_evaluations": "data/trading/candidate_evaluations",
    "execution_reports": "data/trading/execution_reports",
    "trading_diagnostics": "data/trading/diagnostics",
    "operator_actions": "data/trading/operator_actions",
    "paper_autopilot": "data/trading/autopilot",
    "near_miss": "data/trading/near_miss",
    "per_symbol_forecast": "data/trading/per_symbol_forecast",
    "holding_period": "data/trading/holding_period",
}


def project_root(root: Optional[Path] = None) -> Path:
    return Path(root).resolve() if root else REPO_ROOT


def data_path(root: Optional[Path], key: str) -> Path:
    return project_root(root) / DATA_DIRS[key]


def latest_file(root: Optional[Path], key: str, pattern: str, fallback_keys: Iterable[str] = ()) -> Optional[Path]:
    locations = [key, *fallback_keys]
    matches: list[Path] = []
    for location in locations:
        directory = data_path(root, location)
        if directory.exists():
            matches.extend(directory.glob(pattern))
    matches = [path for path in matches if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def file_status(path: Optional[Path], label: str) -> dict:
    if path is None:
        return {"label": label, "path": "", "name": "Missing", "exists": False, "timestamp": ""}
    return {
        "label": label,
        "path": str(path),
        "name": path.name,
        "exists": True,
        "timestamp": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def safe_read_csv(path: Optional[Path], nrows: Optional[int] = None, **kwargs) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, nrows=nrows, low_memory=False, **kwargs)
    except Exception:
        return pd.DataFrame()


def latest_row_by_value(
    path: Optional[Path],
    column: str,
    value: str,
    *,
    chunksize: int = 100_000,
    usecols=None,
) -> dict:
    if path is None or not path.exists():
        return {}
    target = str(value or "").strip().upper()
    if not target:
        return {}
    try:
        for chunk in pd.read_csv(path, chunksize=chunksize, low_memory=False, usecols=usecols):
            if column not in chunk.columns:
                return {}
            rows = chunk[chunk[column].fillna("").astype(str).str.upper().eq(target)]
            if not rows.empty:
                latest = rows.tail(1)
        return latest.iloc[-1].to_dict() if "latest" in locals() else {}
    except Exception:
        return {}


def count_rows(path: Optional[Path]) -> int:
    if path is None or not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except Exception:
        return 0


def count_rows_fast(path: Optional[Path], *, exact_max_bytes: int = 50_000_000, sample_lines: int = 1000) -> int:
    if path is None or not path.exists():
        return 0
    try:
        size = path.stat().st_size
        if size <= exact_max_bytes:
            return count_rows(path)
        with path.open("rb") as handle:
            header = handle.readline()
            rows = []
            for _ in range(sample_lines):
                line = handle.readline()
                if not line:
                    break
                rows.append(line)
        if not rows:
            return 0
        avg_row_bytes = sum(len(line) for line in rows) / len(rows)
        return max(0, int((size - len(header)) / avg_row_bytes))
    except Exception:
        return 0


def readable_reason(value: object) -> str:
    mapping = {
        "model_not_decision_grade": "Model not decision-grade",
        "weak_probability": "Probability below decision threshold",
        "insufficient_validation_samples": "Insufficient validation samples",
        "validated_hit_rate_below_threshold": "Validated hit rate below threshold",
        "expected_trade_return_below_threshold": "Expected trade return below threshold",
        "not_in_top_ranked_long_or_short_candidates": "Not ranked strongly enough today",
        "historical_bucket_avg_gain_below_threshold": "Historical bucket average gain below threshold",
        "icir_below_threshold": "ICIR below deployment threshold",
        "fold_hit_rate_below_floor": "Fold hit rate below stability floor",
        "turnover_adjusted_return_below_threshold": "Turnover-adjusted return below threshold",
        "thin_liquidity": "Liquidity below trading threshold",
        "sector_concentration_breach": "Sector concentration limit reached",
        "stale_features": "Features are stale",
        "regime_unfavourable": "Market regime unfavourable",
        "diagnostic_only": "Diagnostic only",
    }
    text = str(value or "").strip()
    if not text:
        return "Not provided"
    parts = [mapping.get(part.strip(), format_reasons(part.strip())) for part in text.split("|")]
    return "; ".join(parts)
