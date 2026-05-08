from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, RAW_DIR, ensure_data_dirs, latest_file, timestamp

STORE_FILE = RAW_DIR / "03_us_price_history_store.csv"


def latest_tradable_universe_file() -> Path:
    files = [
        p for p in INTERIM_DIR.glob("02_us_tradable_universe_*.csv")
        if "summary" not in p.name and "nasdaq_only" not in p.name
    ]
    if not files:
        raise FileNotFoundError("No 02_us_tradable_universe_*.csv file found. Run universe pipeline first.")
    return max(files, key=lambda p: p.stat().st_mtime)


def read_tradable_universe(limit: Optional[int] = None) -> pd.DataFrame:
    path = latest_tradable_universe_file()
    df = pd.read_csv(path, dtype=str)
    if "yahoo_ticker" not in df.columns:
        raise ValueError(f"{path} missing yahoo_ticker column")
    df["yahoo_ticker"] = df["yahoo_ticker"].astype(str).str.upper().str.strip()
    df = df[df["yahoo_ticker"].ne("")].drop_duplicates("yahoo_ticker")
    if limit:
        df = df.head(limit)
    log(f"Loaded tradable universe: {path} ({len(df):,} tickers)")
    return df


def load_price_store(store_file: Path = STORE_FILE) -> pd.DataFrame:
    if not store_file.exists():
        return pd.DataFrame()
    df = pd.read_csv(store_file, parse_dates=["date"], low_memory=False)
    if df.empty:
        return df
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["ticker", "date"])
    return df


def save_price_store(df: pd.DataFrame, store_file: Path = STORE_FILE) -> None:
    store_file.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["ticker", "date"])
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out = out.sort_values(["ticker", "date"])
    out = out.drop_duplicates(["ticker", "date"], keep="last")
    out.to_csv(store_file, index=False)
    log(f"Updated canonical price store: {store_file} ({len(out):,} rows)")


def determine_download_plan(
    tickers: Iterable[str],
    store: pd.DataFrame,
    start_date: str,
    force_full: bool = False,
) -> Tuple[Dict[str, str], bool]:
    tickers = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    full_mode = force_full or store.empty

    if full_mode:
        return {t: start_date for t in tickers}, True

    latest_by_ticker = store.groupby("ticker")["date"].max().to_dict()
    plan = {}

    today = pd.Timestamp(date.today())

    for ticker in tickers:
        if ticker not in latest_by_ticker or pd.isna(latest_by_ticker[ticker]):
            plan[ticker] = start_date
            continue

        next_date = pd.Timestamp(latest_by_ticker[ticker]) + pd.Timedelta(days=1)

        # Keep a 5-calendar-day overlap to handle delayed Yahoo adjustments,
        # holidays, and occasional missing latest rows.
        overlap_date = max(pd.Timestamp(start_date), next_date - pd.Timedelta(days=5))

        if overlap_date <= today:
            plan[ticker] = overlap_date.strftime("%Y-%m-%d")

    return plan, False


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def normalize_yfinance_download(data: pd.DataFrame, tickers: List[str], download_timestamp: str) -> pd.DataFrame:
    rows = []

    if data is None or data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        top_level = list(data.columns.get_level_values(0).unique())

        # yfinance may return either ticker-first or field-first multi-index.
        ticker_first = any(t in top_level for t in tickers)

        for ticker in tickers:
            try:
                if ticker_first:
                    sub = data[ticker].copy()
                else:
                    sub = data.xs(ticker, axis=1, level=1).copy()
            except Exception:
                continue

            sub = sub.reset_index()
            sub["ticker"] = ticker
            rows.append(sub)
    else:
        if len(tickers) != 1:
            return pd.DataFrame()
        sub = data.copy().reset_index()
        sub["ticker"] = tickers[0]
        rows.append(sub)

    if not rows:
        return pd.DataFrame()

    out = pd.concat(rows, ignore_index=True)

    rename = {
        "Date": "date",
        "Datetime": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Adj_Close": "adj_close",
        "Volume": "volume",
    }

    out = out.rename(columns={c: rename.get(c, c) for c in out.columns})

    required = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
    for c in required:
        if c not in out.columns:
            out[c] = pd.NA

    out = out[required].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()

    for c in ["open", "high", "low", "close", "adj_close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out["source"] = "yfinance"
    out["download_timestamp"] = download_timestamp

    out = out.dropna(subset=["date", "ticker"])
    out = out.drop_duplicates(["ticker", "date"], keep="last")
    return out


def download_group(tickers: List[str], start: str, batch_size: int, sleep_seconds: float) -> Tuple[pd.DataFrame, pd.DataFrame]:
    stamp = datetime.now().isoformat(timespec="seconds")
    all_rows = []
    failures = []

    batches = list(chunked(tickers, batch_size))
    for batch_no, batch in enumerate(batches, start=1):
        log(f"Downloading batch {batch_no}/{len(batches)} start={start} tickers={len(batch)}")

        try:
            import yfinance as yf

            data = yf.download(
                tickers=batch,
                start=start,
                auto_adjust=False,
                group_by="ticker",
                progress=False,
                threads=True,
            )
            normalized = normalize_yfinance_download(data, batch, stamp)

            if normalized.empty:
                for t in batch:
                    failures.append({"ticker": t, "start": start, "reason": "empty_download"})
            else:
                got = set(normalized["ticker"].unique())
                missing = sorted(set(batch) - got)
                for t in missing:
                    failures.append({"ticker": t, "start": start, "reason": "missing_from_batch_result"})
                all_rows.append(normalized)

        except Exception as e:
            for t in batch:
                failures.append({"ticker": t, "start": start, "reason": str(e)[:500]})

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    prices = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    failed = pd.DataFrame(failures)
    return prices, failed


def download_price_history(
    start_date: str = "2018-01-01",
    batch_size: int = 75,
    sleep_seconds: float = 1.0,
    limit: Optional[int] = None,
    force_full: bool = False,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()

    universe = read_tradable_universe(limit=limit)
    tickers = universe["yahoo_ticker"].dropna().astype(str).str.upper().unique().tolist()

    store = load_price_store()
    plan, full_mode = determine_download_plan(tickers, store, start_date=start_date, force_full=force_full)

    mode = "full" if full_mode else "delta"
    log(f"Download mode: {mode}")
    log(f"Tickers in universe: {len(tickers):,}")
    log(f"Tickers requiring download: {len(plan):,}")

    all_new = []
    all_failures = []

    grouped = {}
    for ticker, start in plan.items():
        grouped.setdefault(start, []).append(ticker)

    for start, group_tickers in sorted(grouped.items()):
        prices, failed = download_group(group_tickers, start=start, batch_size=batch_size, sleep_seconds=sleep_seconds)
        if not prices.empty:
            all_new.append(prices)
        if not failed.empty:
            all_failures.append(failed)

    new_prices = pd.concat(all_new, ignore_index=True) if all_new else pd.DataFrame(
        columns=["date", "ticker", "open", "high", "low", "close", "adj_close", "volume", "source", "download_timestamp"]
    )

    failures = pd.concat(all_failures, ignore_index=True) if all_failures else pd.DataFrame(
        columns=["ticker", "start", "reason"]
    )

    if not new_prices.empty:
        new_prices["date"] = pd.to_datetime(new_prices["date"], errors="coerce")
        combined = pd.concat([store, new_prices], ignore_index=True) if not store.empty else new_prices
        save_price_store(combined)
    else:
        combined = store
        if not combined.empty:
            save_price_store(combined)

    run_file = RAW_DIR / f"03_us_price_history_{mode}_{stamp}.csv"
    new_prices.to_csv(run_file, index=False)
    log(f"Wrote {mode} price download file: {run_file} ({len(new_prices):,} rows)")

    failures_file = INTERIM_DIR / f"03_us_price_download_failures_{stamp}.csv"
    failures.to_csv(failures_file, index=False)
    log(f"Wrote failures file: {failures_file} ({len(failures):,} rows)")

    return {
        "run_price_file": run_file,
        "store_file": STORE_FILE,
        "failures_file": failures_file,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", default="2018-01-01")
    p.add_argument("--batch-size", type=int, default=75)
    p.add_argument("--sleep-seconds", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--force-full", action="store_true")
    args = p.parse_args()

    paths = download_price_history(
        start_date=args.start_date,
        batch_size=args.batch_size,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
        force_full=args.force_full,
    )

    for name, path in paths.items():
        log(f"{name}: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
