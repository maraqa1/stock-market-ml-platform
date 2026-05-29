from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, latest_file


def _num(value: Any, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return float(parsed)


def _latest_validated(root: Path) -> Path | None:
    return latest_file(root / "data" / "interim", "03_us_price_validated_universe_*.csv")


def _latest_metadata(root: Path) -> Path | None:
    return latest_file(root / "data" / "interim", "04_us_metadata_enriched_*.csv")


def build_same_day_universe(
    as_of_date: date,
    *,
    root: Path | None = None,
    validated: pd.DataFrame | None = None,
    metadata: pd.DataFrame | None = None,
    halted_symbols: set[str] | None = None,
) -> list[str]:
    base = root or PROJECT_ROOT
    if validated is None:
        path = _latest_validated(base)
        validated = pd.read_csv(path, low_memory=False) if path and path.exists() else pd.DataFrame()
    if metadata is None:
        path = _latest_metadata(base)
        metadata = pd.read_csv(path, low_memory=False) if path and path.exists() else pd.DataFrame()
    if validated.empty:
        return []

    frame = validated.copy()
    if "symbol" not in frame.columns and "ticker" in frame.columns:
        frame = frame.rename(columns={"ticker": "symbol"})
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()

    if not metadata.empty:
        meta = metadata.copy()
        if "symbol" not in meta.columns and "ticker" in meta.columns:
            meta = meta.rename(columns={"ticker": "symbol"})
        meta["symbol"] = meta["symbol"].astype(str).str.upper().str.strip()
        keep_cols = [column for column in ["symbol", "market_cap"] if column in meta.columns]
        if keep_cols:
            frame = frame.merge(meta[keep_cols].drop_duplicates("symbol"), on="symbol", how="left", suffixes=("", "_meta"))
            if "market_cap_meta" in frame.columns and "market_cap" in frame.columns:
                frame["market_cap"] = frame["market_cap"].fillna(frame["market_cap_meta"])

    halted = {symbol.upper() for symbol in (halted_symbols or set())}
    out = []
    for row in frame.fillna("").to_dict("records"):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or symbol in halted:
            continue
        price = _num(row.get("close") or row.get("last_price") or row.get("price"))
        avg_dollar_volume = _num(row.get("avg_dollar_volume_20d") or row.get("average_dollar_volume_20d"))
        market_cap = _num(row.get("market_cap"))
        is_halted = str(row.get("is_halted") or "").strip().lower() in {"true", "1", "yes"}
        if is_halted:
            continue
        if avg_dollar_volume < 20_000_000:
            continue
        if not 5 <= price <= 500:
            continue
        if market_cap < 500_000_000:
            continue
        out.append(symbol)
    return sorted(set(out))
