from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml
from sqlalchemy import insert
from sqlalchemy.engine import Engine

from stockml.common.paths import PER_SYMBOL_FORECAST_DIR, PROJECT_ROOT, latest_file, timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import forecast_cap_log
from stockml.trading.per_symbol_forecast.confirmation import confirmation_fields
from stockml.trading.per_symbol_forecast.derived import canonical_symbol, derived_fields
from stockml.trading.per_symbol_forecast.model_stub import model_fields
from stockml.trading.per_symbol_forecast.schema import OUTPUT_COLUMNS, output_record
from stockml.trading.per_symbol_forecast.statistical import ForecastBounds, rank_to_return_slope_bps, statistical_fields
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


def latest_positions_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else PROJECT_ROOT
    return latest_file(base / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv")


def latest_per_symbol_forecast_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else PROJECT_ROOT
    return latest_file(base / "data" / "trading" / OUTPUT_DIR_NAME, f"{OUTPUT_FILE_PREFIX}_*.csv")


def _forecast_bounds(root: Path | None = None) -> ForecastBounds:
    base = Path(root).resolve() if root else PROJECT_ROOT
    path = base / "config" / "per_symbol_forecast.yaml"
    if not path.exists():
        return ForecastBounds()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ForecastBounds()
    section = payload.get("per_symbol_forecast") if isinstance(payload, dict) else {}
    bounds = section.get("forecast_bounds") if isinstance(section, dict) else {}
    if not isinstance(bounds, dict):
        return ForecastBounds()
    defaults = ForecastBounds()
    return ForecastBounds(
        reasonable_max_1d_return_bps=float(bounds.get("reasonable_max_1d_return_bps", defaults.reasonable_max_1d_return_bps)),
        reasonable_max_5d_return_bps=float(bounds.get("reasonable_max_5d_return_bps", defaults.reasonable_max_5d_return_bps)),
        reasonable_max_move_bps=float(bounds.get("reasonable_max_move_bps", defaults.reasonable_max_move_bps)),
        suspicious_warn_threshold_bps=float(bounds.get("suspicious_warn_threshold_bps", defaults.suspicious_warn_threshold_bps)),
        cap_at_max=bool(bounds.get("cap_at_max", defaults.cap_at_max)),
        max_reasonable_slope_bps_per_unit=float(bounds.get("max_reasonable_slope_bps_per_unit", defaults.max_reasonable_slope_bps_per_unit)),
    )


def _candidate_rows(frame: pd.DataFrame, limit: int) -> Iterable[dict[str, object]]:
    if frame.empty:
        return []
    working = frame.copy()
    if "candidate_rank" in working.columns:
        working["_forecast_rank_sort"] = pd.to_numeric(working["candidate_rank"], errors="coerce")
        working = working.sort_values("_forecast_rank_sort", na_position="last")
    open_mask = working.get("__is_open_position", pd.Series(False, index=working.index)).fillna(False).astype(bool)
    selected = pd.concat([working[~open_mask].head(limit), working[open_mask]]).drop_duplicates("symbol", keep="last")
    return selected.fillna("").to_dict("records")


def _merge_candidate_and_position_rows(candidates: pd.DataFrame, positions: pd.DataFrame | None = None) -> pd.DataFrame:
    candidates = candidates.copy() if candidates is not None and not candidates.empty else pd.DataFrame()
    positions = positions.copy() if positions is not None and not positions.empty else pd.DataFrame()
    if not candidates.empty:
        candidates["__symbol"] = candidates.get("symbol", pd.Series("", index=candidates.index)).fillna("").astype(str).str.upper()
        candidates["__forecast_scope"] = "candidate"
        candidates["__is_open_position"] = False
    if not positions.empty:
        positions["__symbol"] = positions.get("symbol", pd.Series("", index=positions.index)).fillna("").astype(str).str.upper()
        positions["__forecast_scope"] = "open_position"
        positions["__is_open_position"] = True
        if "side" in positions.columns:
            positions["side"] = positions["side"].fillna("").astype(str).str.lower().map({"long": "buy", "short": "sell"}).fillna(positions["side"])
        if "trade_action" not in positions.columns:
            positions["trade_action"] = positions.get("side", pd.Series("", index=positions.index)).fillna("").astype(str).str.lower().map({"buy": "Long", "sell": "Short", "long": "Long", "short": "Short"}).fillna("")
    if candidates.empty:
        return positions.drop(columns=["__symbol"], errors="ignore")
    if positions.empty:
        return candidates.drop(columns=["__symbol"], errors="ignore")
    pos_by_symbol = {str(row.get("__symbol")): row for row in positions.fillna("").to_dict("records") if row.get("__symbol")}
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in candidates.fillna("").to_dict("records"):
        symbol = str(row.get("__symbol") or "")
        merged = dict(row)
        if symbol in pos_by_symbol:
            pos = pos_by_symbol[symbol]
            for key, value in pos.items():
                if key not in merged or merged.get(key) in {"", None}:
                    merged[key] = value
            merged["__forecast_scope"] = "candidate_and_open_position"
            merged["__is_open_position"] = True
        records.append(merged)
        seen.add(symbol)
    for symbol, row in pos_by_symbol.items():
        if symbol not in seen:
            records.append(dict(row))
    return pd.DataFrame(records).drop(columns=["__symbol"], errors="ignore")


def forecast_rows(
    candidates: pd.DataFrame,
    positions: pd.DataFrame | None = None,
    generated_at: str | None = None,
    limit: int = 100,
    bounds: ForecastBounds | None = None,
) -> pd.DataFrame:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    cfg = bounds or ForecastBounds()
    slope_5d = rank_to_return_slope_bps(candidates.rename(columns={"risk_adjusted_score": "model_score"}), "expected_trade_return")
    source_rows = _merge_candidate_and_position_rows(candidates, positions)
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in _candidate_rows(source_rows, limit):
        symbol = canonical_symbol(row)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        record: dict[str, object] = {}
        record.update(derived_fields(row, generated_at=generated_at))
        record.update(statistical_fields(row, slope_5d=slope_5d, bounds=cfg))
        record.update(confirmation_fields(record))
        record.update(model_fields())
        records.append(output_record(record))
    return validate_output(pd.DataFrame(records, columns=OUTPUT_COLUMNS))


def log_forecast_caps(frame: pd.DataFrame, *, engine: Engine | None = None, forecast_run_id: str = "", now: datetime | None = None) -> int:
    if frame.empty or "cap_applied" not in frame.columns:
        return 0
    capped = frame[frame["cap_applied"].fillna(False).astype(bool)].copy()
    if capped.empty:
        return 0
    db = engine or get_engine(required=False)
    if db is None:
        return 0
    stamp = now or datetime.now(timezone.utc)
    rows = []
    for row in capped.fillna("").to_dict("records"):
        pre_cap = row.get("pre_cap_expected_5d_bps")
        if pre_cap in ["", None]:
            continue
        rows.append(
            {
                "logged_at": stamp,
                "symbol": str(row.get("symbol") or "").upper(),
                "field_name": "expected_5d_return_bps",
                "pre_cap_value": float(pre_cap),
                "cap_applied": float(row.get("expected_5d_return_bps") or 0),
                "reason": "forecast_return_cap_applied",
                "forecast_run_id": forecast_run_id,
            }
        )
    if not rows:
        return 0
    try:
        with db.begin() as conn:
            conn.execute(insert(forecast_cap_log), rows)
    except Exception:
        return 0
    return len(rows)


def write_per_symbol_forecast(frame: pd.DataFrame, output_dir: Path | None = None, stamp: str | None = None) -> Path:
    directory = output_dir or PER_SYMBOL_FORECAST_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{OUTPUT_FILE_PREFIX}_{stamp or timestamp()}.csv"
    validate_output(frame).to_csv(path, index=False)
    return path


def generate_per_symbol_forecast(root: Path | None = None, limit: int = 100, stamp: str | None = None) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    candidate_path = latest_candidate_pool_path(base)
    positions_path = latest_positions_path(base)
    candidates = _read_csv(candidate_path)
    positions = _read_csv(positions_path)
    run_id = stamp or timestamp()
    frame = forecast_rows(candidates, positions=positions, limit=limit, bounds=_forecast_bounds(base))
    output_path = write_per_symbol_forecast(frame, output_dir=base / "data" / "trading" / OUTPUT_DIR_NAME, stamp=run_id)
    caps_logged = log_forecast_caps(frame, forecast_run_id=run_id)
    return {
        "status": "ok",
        "rows": int(len(frame)),
        "path": str(output_path),
        "source": str(candidate_path or ""),
        "positions_source": str(positions_path or ""),
        "caps_logged": caps_logged,
    }
