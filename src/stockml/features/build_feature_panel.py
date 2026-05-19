from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, PROCESSED_DIR, RAW_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.features.liquidity_features import add_liquidity_features
from stockml.features.market_context_features import add_market_context_features
from stockml.features.sector_features import add_sector_features
from stockml.features.technical_features import add_technical_features
from stockml.features.volatility_features import add_volatility_features

FEATURE_PANEL_COLUMNS = [
    "date", "ticker", "company", "exchange", "sector", "industry", "open", "high", "low", "close", "adj_close",
    "volume", "dollar_volume", "avg_dollar_volume_20d", "return_1d", "return_5d", "return_10d", "return_20d",
    "return_60d", "volatility_20d", "volatility_60d", "volume_ratio_20d", "high_20d", "low_20d",
    "distance_from_20d_high", "distance_from_20d_low", "sma_20", "sma_50", "sma_200", "sma_gap_20_50",
    "sma_gap_50_200", "rsi_14", "macd", "macd_signal", "macd_hist", "sector_return_5d", "sector_return_20d",
    "relative_return_vs_sector_5d", "relative_return_vs_sector_20d", "market_return_5d", "market_return_20d",
    "feature_missing_ratio", "market_volatility_20d", "market_regime_score", "risk_on_risk_off_flag",
    "downside_volatility_20d", "max_drawdown_60d", "liquidity_score", "volatility_score", "risk_score",
    "sector_momentum_rank", "sector_relative_strength_score", "sector_relative_momentum_score", "volume_confirmation_score",
]


def build_feature_panel_from_frames(prices: pd.DataFrame, universe: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    out = prices.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "ticker"]).sort_values(["ticker", "date"])

    universe_key = universe.copy()
    if "yahoo_ticker" in universe_key.columns:
        universe_key["ticker"] = universe_key["yahoo_ticker"]
    keep = [c for c in ["ticker", "company", "listing_exchange"] if c in universe_key.columns]
    universe_key = universe_key[keep].drop_duplicates("ticker") if keep else pd.DataFrame(columns=["ticker"])
    if "listing_exchange" in universe_key.columns:
        universe_key = universe_key.rename(columns={"listing_exchange": "exchange"})

    meta = metadata.copy()
    if not meta.empty:
        meta["ticker"] = meta["ticker"].astype(str).str.upper().str.strip()
        meta = meta.drop_duplicates("ticker", keep="last")

    out = out.merge(universe_key, on="ticker", how="left")
    out = out.merge(meta, on="ticker", how="left", suffixes=("", "_metadata"))
    for col in ["company", "exchange"]:
        meta_col = f"{col}_metadata"
        if meta_col in out.columns:
            out[col] = out[col].fillna(out[meta_col])
            out = out.drop(columns=[meta_col])

    out = add_liquidity_features(out)
    out = add_technical_features(out)
    out = add_volatility_features(out)
    out = add_sector_features(out)
    out = add_market_context_features(out)

    score_cols = ["return_20d", "sector_relative_strength_score", "volume_confirmation_score", "liquidity_score"]
    out["momentum_score"] = out.groupby("date")["return_20d"].rank(pct=True).fillna(0.5)
    out["technical_setup_score"] = out.groupby("date")["sma_gap_20_50"].rank(pct=True).fillna(0.5)
    out["feature_missing_ratio"] = out[score_cols].isna().mean(axis=1).round(4)

    for col in FEATURE_PANEL_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[FEATURE_PANEL_COLUMNS].sort_values(["date", "ticker"]).reset_index(drop=True)


def latest_metadata_file() -> Path:
    path = latest_file(INTERIM_DIR, "04_us_metadata_enriched_*.csv")
    if path is None:
        raise FileNotFoundError("No 04_us_metadata_enriched_*.csv file found. Run metadata pipeline first.")
    return path


def _filter_universe_exchange(universe: pd.DataFrame, exchange: str | None) -> pd.DataFrame:
    if not exchange or "listing_exchange" not in universe.columns:
        return universe
    target = str(exchange).upper().strip()
    return universe[universe["listing_exchange"].astype(str).str.upper().str.strip().eq(target)].copy()


def _universe_ticker_column(universe: pd.DataFrame) -> str:
    return "yahoo_ticker" if "yahoo_ticker" in universe.columns else "ticker"


def build_feature_panel(limit_tickers: Optional[int] = None, exchange: str | None = None) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    price_path = RAW_DIR / "03_us_price_history_store.csv"
    universe_path = latest_file(INTERIM_DIR, "03_us_price_validated_universe_*.csv")
    if universe_path is None:
        raise FileNotFoundError("No 03_us_price_validated_universe_*.csv file found. Run price validation first.")
    if not price_path.exists():
        raise FileNotFoundError(f"Missing price store: {price_path}")

    prices = pd.read_csv(price_path, low_memory=False)
    universe = pd.read_csv(universe_path, dtype=str)
    metadata = pd.read_csv(latest_metadata_file(), low_memory=False)
    universe = _filter_universe_exchange(universe, exchange)

    ticker_col = _universe_ticker_column(universe)
    scope_tickers = universe[ticker_col].astype(str).str.upper().str.strip()

    if limit_tickers:
        universe = universe.head(limit_tickers).copy()
        scope_tickers = universe[ticker_col].astype(str).str.upper().str.strip()

    tickers = scope_tickers.dropna().tolist()
    if exchange or limit_tickers:
        prices = prices[prices["ticker"].astype(str).str.upper().str.strip().isin(tickers)].copy()

    panel = build_feature_panel_from_frames(prices, universe, metadata)
    panel_path = PROCESSED_DIR / f"05_us_feature_panel_{stamp}.csv"
    panel.to_csv(panel_path, index=False)
    log(f"Wrote feature panel: {panel_path} ({len(panel):,} rows)")

    quality = panel.groupby("ticker", as_index=False).agg(
        row_count=("date", "size"),
        min_date=("date", "min"),
        max_date=("date", "max"),
        avg_feature_missing_ratio=("feature_missing_ratio", "mean"),
    )
    quality_path = INTERIM_DIR / f"05_us_feature_quality_{stamp}.csv"
    quality.to_csv(quality_path, index=False)
    log(f"Wrote feature quality: {quality_path} ({len(quality):,} rows)")
    return {"feature_panel": panel_path, "feature_quality": quality_path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--exchange", default=None, help="Optional listing exchange filter, e.g. NYSE")
    args = parser.parse_args()
    paths = build_feature_panel(limit_tickers=args.limit_tickers, exchange=args.exchange)
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
