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
    "operator_actions": "data/trading/operator_actions",
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


def count_rows(path: Optional[Path]) -> int:
    if path is None or not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return max(0, sum(1 for _ in handle) - 1)
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
