from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, RAW_DIR, ensure_data_dirs, timestamp
from stockml.prices.download_price_history import STORE_FILE, latest_tradable_universe_file


def build_price_quality_report(
    min_trading_days: int = 252,
    min_price: float = 3.0,
    min_avg_dollar_volume_20d: float = 5_000_000.0,
    provider_name: str | None = None,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()

    if not STORE_FILE.exists():
        raise FileNotFoundError(f"Missing price store: {STORE_FILE}")

    prices = pd.read_csv(STORE_FILE, parse_dates=["date"], low_memory=False)
    prices["ticker"] = prices["ticker"].astype(str).str.upper().str.strip()
    if provider_name and "source" in prices.columns:
        prices = prices[prices["source"].astype(str).str.strip().eq(str(provider_name).strip())].copy()
    prices = prices.sort_values(["ticker", "date"])

    latest_rows = prices.groupby("ticker").tail(1).copy()
    latest_rows["latest_close"] = latest_rows["close"]
    latest_rows["latest_volume"] = latest_rows["volume"]

    prices["dollar_volume"] = prices["close"] * prices["volume"]
    prices["avg_dollar_volume_20d"] = prices.groupby("ticker")["dollar_volume"].transform(
        lambda x: x.rolling(20, min_periods=5).mean()
    )

    latest_liq = prices.groupby("ticker").tail(1)[["ticker", "avg_dollar_volume_20d"]]

    quality = prices.groupby("ticker").agg(
        row_count=("date", "size"),
        min_date=("date", "min"),
        max_date=("date", "max"),
        missing_close_count=("close", lambda x: int(x.isna().sum())),
        missing_volume_count=("volume", lambda x: int(x.isna().sum())),
    ).reset_index()

    quality = quality.merge(
        latest_rows[["ticker", "latest_close", "latest_volume"]],
        on="ticker",
        how="left",
    )
    quality = quality.merge(latest_liq, on="ticker", how="left")

    quality["trading_days"] = quality["row_count"]
    quality["passes_min_history"] = quality["trading_days"] >= min_trading_days
    quality["passes_price_filter"] = quality["latest_close"] >= min_price
    quality["passes_liquidity_filter"] = quality["avg_dollar_volume_20d"] >= min_avg_dollar_volume_20d

    quality["price_quality_status"] = "pass"
    quality.loc[~quality["passes_min_history"], "price_quality_status"] = "fail_min_history"
    quality.loc[~quality["passes_price_filter"], "price_quality_status"] = "fail_price"
    quality.loc[~quality["passes_liquidity_filter"], "price_quality_status"] = "fail_liquidity"

    quality_path = INTERIM_DIR / f"03_us_price_history_quality_{stamp}.csv"
    quality.to_csv(quality_path, index=False)
    log(f"Wrote price quality report: {quality_path} ({len(quality):,} rows)")

    universe = pd.read_csv(latest_tradable_universe_file(), dtype=str)
    universe["yahoo_ticker"] = universe["yahoo_ticker"].astype(str).str.upper().str.strip()

    validated = universe.merge(
        quality,
        left_on="yahoo_ticker",
        right_on="ticker",
        how="left",
    )

    validated = validated[validated["price_quality_status"].eq("pass")].copy()

    validated_path = INTERIM_DIR / f"03_us_price_validated_universe_{stamp}.csv"
    validated.to_csv(validated_path, index=False)
    log(f"Wrote price validated universe: {validated_path} ({len(validated):,} rows)")

    return {
        "quality_report": quality_path,
        "validated_universe": validated_path,
    }


def main() -> int:
    paths = build_price_quality_report()
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
