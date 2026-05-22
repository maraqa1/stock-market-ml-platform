from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.exchange_scope import exchange_scope_label, filter_listing_exchange
from stockml.common.paths import INTERIM_DIR, RAW_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.marketdata.providers.factory import provider_from_name
from stockml.marketdata.schemas import PRICE_COLUMNS

STORE_FILE = RAW_DIR / "03_us_price_history_store.csv"


def latest_tradable_universe_file() -> Path:
    files = [
        p for p in INTERIM_DIR.glob("02_us_tradable_universe_*.csv")
        if "summary" not in p.name and "nasdaq_only" not in p.name
    ]
    if not files:
        raise FileNotFoundError("No 02_us_tradable_universe_*.csv file found. Run universe pipeline first.")
    return max(files, key=lambda p: p.stat().st_mtime)


def read_tradable_universe(limit: Optional[int] = None, exchange: object = None) -> pd.DataFrame:
    path = latest_tradable_universe_file()
    df = pd.read_csv(path, dtype=str)
    if "yahoo_ticker" not in df.columns:
        raise ValueError(f"{path} missing yahoo_ticker column")
    df["yahoo_ticker"] = df["yahoo_ticker"].astype(str).str.upper().str.strip()
    df = df[df["yahoo_ticker"].ne("")].drop_duplicates("yahoo_ticker")
    if exchange:
        if "listing_exchange" not in df.columns:
            raise ValueError(f"{path} missing listing_exchange column required by --exchange")
        df["listing_exchange"] = df["listing_exchange"].astype(str).str.upper().str.strip()
        df = filter_listing_exchange(df, exchange=exchange)
    if limit:
        df = df.head(limit)
    scope = f" exchange={exchange_scope_label(exchange)}" if exchange else ""
    log(f"Loaded tradable universe: {path} ({len(df):,} tickers{scope})")
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
    provider_name: str | None = None,
) -> Tuple[Dict[str, str], bool]:
    tickers = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    if provider_name and not store.empty and "source" in store.columns:
        clean_provider = str(provider_name).strip()
        store = store[store["source"].astype(str).str.strip().eq(clean_provider)].copy()
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

        # Keep a 5-calendar-day overlap to handle delayed provider adjustments,
        # holidays, and occasional missing latest rows.
        overlap_date = max(pd.Timestamp(start_date), next_date - pd.Timedelta(days=5))

        if overlap_date <= today:
            plan[ticker] = overlap_date.strftime("%Y-%m-%d")

    return plan, False


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def download_group(tickers: List[str], start: str, batch_size: int, sleep_seconds: float, provider_name: str | None = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    stamp = datetime.now().isoformat(timespec="seconds")
    all_rows = []
    failures = []
    provider = provider_from_name(provider_name)

    batches = list(chunked(tickers, batch_size))
    for batch_no, batch in enumerate(batches, start=1):
        log(f"Downloading batch {batch_no}/{len(batches)} provider={provider.provider_name} start={start} tickers={len(batch)}")

        normalized, failed = provider.fetch_daily_prices(batch, start=start, download_timestamp=stamp)
        requested = {str(t).upper().strip() for t in batch if str(t).strip()}
        returned = set()
        failed_tickers = set()

        if not normalized.empty:
            returned = set(normalized["ticker"].dropna().astype(str).str.upper().str.strip())
            all_rows.append(normalized)
        if not failed.empty:
            failed_tickers = set(failed["ticker"].dropna().astype(str).str.upper().str.strip())
            failures.extend(failed.to_dict("records"))
        for missing in sorted(requested - returned - failed_tickers):
            failures.append({"ticker": missing, "start": start, "reason": "provider_returned_no_rows_or_failure"})

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
    exchange: object = None,
    provider_name: str | None = None,
    symbols: Iterable[str] | None = None,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()

    if symbols:
        tickers = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        log(f"Loaded explicit symbol repair list: {len(tickers):,} tickers")
    else:
        universe = read_tradable_universe(limit=limit, exchange=exchange)
        tickers = universe["yahoo_ticker"].dropna().astype(str).str.upper().unique().tolist()

    store = load_price_store()
    provider = provider_from_name(provider_name)
    plan, full_mode = determine_download_plan(
        tickers,
        store,
        start_date=start_date,
        force_full=force_full,
        provider_name=provider.provider_name,
    )

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
        prices, failed = download_group(group_tickers, start=start, batch_size=batch_size, sleep_seconds=sleep_seconds, provider_name=provider.provider_name)
        if not prices.empty:
            all_new.append(prices)
        if not failed.empty:
            all_failures.append(failed)

    new_prices = pd.concat(all_new, ignore_index=True) if all_new else pd.DataFrame(
        columns=PRICE_COLUMNS
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
    p.add_argument("--exchange", default=None, help="Optional listing exchange filter, e.g. NASDAQ")
    p.add_argument("--force-full", action="store_true")
    p.add_argument("--provider", default=None, help="Market data provider: yahoo_legacy or eodhd. Defaults to config/env.")
    p.add_argument("--symbols", nargs="*", default=None, help="Optional explicit ticker list for targeted price repair/backfill.")
    args = p.parse_args()

    paths = download_price_history(
        start_date=args.start_date,
        batch_size=args.batch_size,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
        force_full=args.force_full,
        exchange=args.exchange,
        provider_name=args.provider,
        symbols=args.symbols,
    )

    for name, path in paths.items():
        log(f"{name}: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
