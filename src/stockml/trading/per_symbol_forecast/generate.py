from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import PER_SYMBOL_FORECAST_DIR, PROJECT_ROOT, latest_file, timestamp
from stockml.trading.per_symbol_forecast.derived import canonical_symbol, derived_fields
from stockml.trading.per_symbol_forecast.model_stub import model_fields
from stockml.trading.per_symbol_forecast.schema import OUTPUT_COLUMNS, output_record
from stockml.trading.per_symbol_forecast.statistical import rank_to_return_slope, statistical_fields
from stockml.trading.per_symbol_forecast.validation import validate_output

OUTPUT_DIR_NAME = "per_symbol_forecast"
OUTPUT_FILE_PREFIX = "per_symbol_forecast"


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def latest_candidate_pool_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else PROJECT_ROOT
    return latest_file(base / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")


def latest_per_symbol_forecast_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else PROJECT_ROOT
    return latest_file(base / "data" / "trading" / OUTPUT_DIR_NAME, f"{OUTPUT_FILE_PREFIX}_*.csv")


def _candidate_rows(frame: pd.DataFrame, limit: int) -> Iterable[dict[str, object]]:
    if frame.empty:
        return []
    working = frame.copy()
    if "candidate_rank" in working.columns:
        working["_forecast_rank_sort"] = pd.to_numeric(working["candidate_rank"], errors="coerce")
        working = working.sort_values("_forecast_rank_sort", na_position="last")
    return working.head(limit).fillna("").to_dict("records")


def forecast_rows(candidates: pd.DataFrame, generated_at: str | None = None, limit: int = 100) -> pd.DataFrame:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    slope_5d = rank_to_return_slope(candidates.rename(columns={"risk_adjusted_score": "model_score"}), "expected_trade_return")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in _candidate_rows(candidates, limit):
        symbol = canonical_symbol(row)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        record: dict[str, object] = {}
        record.update(derived_fields(row, generated_at=generated_at))
        record.update(statistical_fields(row, slope_5d=slope_5d))
        record.update(model_fields())
        records.append(output_record(record))
    return validate_output(pd.DataFrame(records, columns=OUTPUT_COLUMNS))


def write_per_symbol_forecast(frame: pd.DataFrame, output_dir: Path | None = None, stamp: str | None = None) -> Path:
    directory = output_dir or PER_SYMBOL_FORECAST_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{OUTPUT_FILE_PREFIX}_{stamp or timestamp()}.csv"
    validate_output(frame).to_csv(path, index=False)
    return path


def generate_per_symbol_forecast(root: Path | None = None, limit: int = 100, stamp: str | None = None) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    candidate_path = latest_candidate_pool_path(base)
    candidates = _read_csv(candidate_path)
    frame = forecast_rows(candidates, limit=limit)
    output_path = write_per_symbol_forecast(frame, output_dir=base / "data" / "trading" / OUTPUT_DIR_NAME, stamp=stamp)
    return {
        "status": "ok",
        "rows": int(len(frame)),
        "path": str(output_path),
        "source": str(candidate_path or ""),
    }
