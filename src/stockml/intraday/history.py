from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import RAW_DIR, ensure_data_dirs
from stockml.marketdata.providers.eodhd import INTRADAY_COLUMNS


INTRADAY_DIR = RAW_DIR / "intraday"
INTRADAY_STORE_FILE = INTRADAY_DIR / "5min_bars_store.csv"


def _utc_timestamp(value: object) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        raise ValueError(f"Invalid timestamp: {value}")
    return parsed


def _date_start(value: date | str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def _date_end(value: date | str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


def _parse_bar_timestamps(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce", utc=True)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        median = float(numeric.dropna().median())
        if median > 1_000_000_000_000:
            return pd.to_datetime(numeric, errors="coerce", utc=True, unit="ms")
        if median > 1_000_000_000:
            return pd.to_datetime(numeric, errors="coerce", utc=True, unit="s")
    return pd.to_datetime(series, errors="coerce", utc=True)


def normalize_intraday_bars(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "ticker" in out.columns and "symbol" not in out.columns:
        out = out.rename(columns={"ticker": "symbol"})
    if "date" in out.columns and "timestamp" not in out.columns:
        out = out.rename(columns={"date": "timestamp"})
    if "datetime" in out.columns and "timestamp" not in out.columns:
        out = out.rename(columns={"datetime": "timestamp"})
    for column in INTRADAY_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out["timestamp"] = _parse_bar_timestamps(out["timestamp"])
    for column in ["open", "high", "low", "close", "volume", "vwap", "spread_bps"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["vwap"] = out["vwap"].fillna(out["close"])
    out = out[INTRADAY_COLUMNS].copy()
    out = out.dropna(subset=["symbol", "timestamp", "open", "high", "low", "close"])
    out = out[out["symbol"].ne("")]
    return out.sort_values(["symbol", "timestamp"]).drop_duplicates(["symbol", "timestamp"], keep="last").reset_index(drop=True)


def load_intraday_store(store_file: Path = INTRADAY_STORE_FILE) -> pd.DataFrame:
    if not store_file.exists():
        return pd.DataFrame(columns=INTRADAY_COLUMNS)
    return normalize_intraday_bars(pd.read_csv(store_file, low_memory=False))


def save_intraday_store(frame: pd.DataFrame, store_file: Path = INTRADAY_STORE_FILE) -> None:
    store_file.parent.mkdir(parents=True, exist_ok=True)
    out = normalize_intraday_bars(frame)
    out.to_csv(store_file, index=False)
    log(f"Updated canonical intraday 5m store: {store_file} ({len(out):,} rows)")


def determine_intraday_download_plan(
    symbols: Iterable[str],
    store: pd.DataFrame,
    *,
    start_date: str,
    end_date: str | None = None,
    force_full: bool = False,
) -> tuple[dict[str, tuple[pd.Timestamp, pd.Timestamp]], bool]:
    clean_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
    end_ts = _date_end(end_date or date.today())
    start_ts = _date_start(start_date)
    full_mode = force_full or store.empty
    if full_mode:
        return {symbol: (start_ts, end_ts) for symbol in clean_symbols}, True

    working = normalize_intraday_bars(store)
    latest_by_symbol = working.groupby("symbol")["timestamp"].max().to_dict() if not working.empty else {}
    plan: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for symbol in clean_symbols:
        latest = latest_by_symbol.get(symbol)
        if latest is None or pd.isna(latest):
            plan[symbol] = (start_ts, end_ts)
            continue
        next_ts = max(start_ts, pd.Timestamp(latest) + pd.Timedelta(minutes=5))
        if next_ts <= end_ts:
            plan[symbol] = (next_ts, end_ts)
    return plan, False


def _download_symbol(provider: object, symbol: str, start: pd.Timestamp, end: pd.Timestamp, download_timestamp: str) -> tuple[pd.DataFrame, dict[str, object] | None]:
    if not hasattr(provider, "fetch_intraday_bars"):
        return pd.DataFrame(columns=INTRADAY_COLUMNS), {
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "reason": f"provider_{getattr(provider, 'provider_name', 'unknown')}_does_not_support_intraday",
        }
    return provider.fetch_intraday_bars(
        symbol,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="5m",
        download_timestamp=download_timestamp,
    )


def _provider_from_name(provider_name: str | None) -> object:
    from stockml.marketdata.providers.factory import provider_from_name

    return provider_from_name(provider_name)


def download_intraday_history(
    *,
    start_date: str,
    end_date: str | None = None,
    provider_name: str | None = "eodhd",
    symbols: Iterable[str] | None = None,
    limit: int | None = None,
    force_full: bool = False,
    sleep_seconds: float = 0.25,
    store_file: Path = INTRADAY_STORE_FILE,
    output_dir: Path = INTRADAY_DIR,
) -> dict[str, Path]:
    ensure_data_dirs()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if symbols:
        clean_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        if limit:
            clean_symbols = clean_symbols[:limit]
        log(f"Loaded explicit intraday symbol list: {len(clean_symbols):,} symbols")
    else:
        from stockml.prices.download_price_history import read_tradable_universe

        universe = read_tradable_universe(limit=limit)
        clean_symbols = universe["yahoo_ticker"].dropna().astype(str).str.upper().str.strip().unique().tolist()

    store = load_intraday_store(store_file)
    plan, full_mode = determine_intraday_download_plan(
        clean_symbols,
        store,
        start_date=start_date,
        end_date=end_date,
        force_full=force_full,
    )
    mode = "full" if full_mode else "delta"
    log(f"Intraday download mode: {mode}")
    log(f"Symbols in intraday universe: {len(clean_symbols):,}")
    log(f"Symbols requiring intraday download: {len(plan):,}")

    provider = _provider_from_name(provider_name)
    download_timestamp = datetime.now().isoformat(timespec="seconds")
    frames: list[pd.DataFrame] = []
    failures: list[dict[str, object]] = []
    for index, (symbol, (start, end)) in enumerate(plan.items(), start=1):
        if index == 1 or index == len(plan) or index % 25 == 0:
            log(f"Intraday progress {index:,}/{len(plan):,} symbol={symbol} start={start.date()} end={end.date()}")
        bars, failure = _download_symbol(provider, symbol, start, end, download_timestamp)
        if not bars.empty:
            frames.append(bars)
        if failure is not None:
            failures.append(failure)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    new_bars = normalize_intraday_bars(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=INTRADAY_COLUMNS)
    combined = pd.concat([store, new_bars], ignore_index=True) if not store.empty else new_bars
    save_intraday_store(combined, store_file)

    run_path = output_dir / f"5min_bars_{mode}_{stamp}.csv"
    new_bars.to_csv(run_path, index=False)
    log(f"Wrote intraday {mode} file: {run_path} ({len(new_bars):,} rows)")

    failures_path = output_dir / f"5min_bars_failures_{stamp}.csv"
    pd.DataFrame(failures, columns=["symbol", "start", "end", "reason"]).to_csv(failures_path, index=False)
    log(f"Wrote intraday failures file: {failures_path} ({len(failures):,} rows)")

    return {"bars_file": run_path, "store_file": store_file, "failures_file": failures_path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--provider", default="eodhd")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--store-file", type=Path, default=INTRADAY_STORE_FILE)
    parser.add_argument("--output-dir", type=Path, default=INTRADAY_DIR)
    args = parser.parse_args()
    paths = download_intraday_history(
        start_date=args.start_date,
        end_date=args.end_date,
        provider_name=args.provider,
        symbols=args.symbols,
        limit=args.limit,
        force_full=args.force_full,
        sleep_seconds=args.sleep_seconds,
        store_file=args.store_file,
        output_dir=args.output_dir,
    )
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0
