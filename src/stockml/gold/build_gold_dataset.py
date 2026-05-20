from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.exchange_scope import filter_listing_exchange
from stockml.common.paths import GOLD_DIR, INTERIM_DIR, PORTAL_OUTPUTS_DIR, PROCESSED_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.gold.gold_quality_checks import build_data_dictionary, build_gold_quality
from stockml.gold.target_engineering import TARGET_COLUMNS, add_ranking_targets

GOLD_COLUMNS = [
    "date", "ticker", "company", "exchange", "sector", "industry", "market_cap", "beta", "country", "currency",
    "open", "high", "low", "close", "adj_close", "volume", "dollar_volume", "avg_dollar_volume_20d", "liquidity_score",
    "return_1d", "return_5d", "return_10d", "return_20d", "return_60d", "sma_20", "sma_50", "sma_200",
    "sma_gap_20_50", "sma_gap_50_200", "rsi_14", "macd", "macd_signal", "macd_hist", "distance_from_20d_high",
    "distance_from_20d_low", "volatility_20d", "volatility_60d", "downside_volatility_20d", "max_drawdown_60d",
    "volatility_score", "risk_score", "sector_return_5d", "sector_return_20d", "relative_return_vs_sector_5d",
    "relative_return_vs_sector_20d", "sector_momentum_rank", "sector_relative_strength_score", "market_return_5d",
    "market_return_20d", "market_volatility_20d", "market_regime_score", "risk_on_risk_off_flag", "article_count",
    "sentiment_score_mean", "sentiment_score_min", "sentiment_score_max", "sentiment_positive_count",
    "sentiment_negative_count", "sentiment_neutral_count", "sentiment_momentum_3d", "sentiment_momentum_7d",
    "sentiment_volume_spike_flag", "news_attention_score", "sentiment_status", "sentiment_source", "data_quality_score",
    "history_quality_score", "momentum_score", "sector_relative_momentum_score", "volume_confirmation_score",
    "sentiment_score", "technical_setup_score", "selection_score", "candidate_rank_overall", "candidate_rank_by_sector",
] + TARGET_COLUMNS

SENTIMENT_INPUT_COLUMNS = {
    "article_count",
    "sentiment_score_mean",
    "sentiment_score_min",
    "sentiment_score_max",
    "sentiment_positive_count",
    "sentiment_negative_count",
    "sentiment_neutral_count",
    "sentiment_status",
    "sentiment_source",
}

FEATURE_EXTRA_COLUMNS = {"feature_missing_ratio"}


def latest_feature_panel_file() -> Path:
    path = latest_file(PROCESSED_DIR, "05_us_feature_panel_*.csv")
    if path is None:
        raise FileNotFoundError("No 05_us_feature_panel_*.csv file found. Run feature pipeline first.")
    return path


def latest_sentiment_panel_file() -> Optional[Path]:
    return latest_file(PROCESSED_DIR, "05_news_sentiment_panel_*.csv")


def _read_feature_panel(path: Path) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0)
    wanted = [col for col in header.columns if col in set(GOLD_COLUMNS) | FEATURE_EXTRA_COLUMNS]
    return pd.read_csv(path, usecols=wanted, low_memory=False)


def _feature_usecols(path: Path) -> list[str]:
    header = pd.read_csv(path, nrows=0)
    return [col for col in header.columns if col in set(GOLD_COLUMNS) | FEATURE_EXTRA_COLUMNS]


def _date_values(values: Iterable[object]) -> set[pd.Timestamp]:
    return set(pd.to_datetime(pd.Series(list(values)), errors="coerce").dropna())


def _feature_date_counts(path: Path, exchange: object = None, chunksize: int = 250_000) -> pd.Series:
    header = pd.read_csv(path, nrows=0)
    usecols = [col for col in ["date", "exchange"] if col in header.columns]
    counts: list[pd.Series] = []
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunksize, low_memory=False):
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
        chunk = filter_listing_exchange(chunk, exchange=exchange, column="exchange")
        counts.append(chunk.groupby("date").size())
    if not counts:
        return pd.Series(dtype="int64")
    return pd.concat(counts, axis=1).fillna(0).sum(axis=1).sort_index().astype(int)


def _date_windows(date_counts: pd.Series, max_rows: int, lookahead_dates: int = 10) -> list[tuple[list[pd.Timestamp], list[pd.Timestamp]]]:
    dates = list(date_counts.index)
    windows: list[tuple[list[pd.Timestamp], list[pd.Timestamp]]] = []
    start = 0
    while start < len(dates):
        total = 0
        end = start
        while end < len(dates) and (total == 0 or total + int(date_counts.iloc[end]) <= max_rows):
            total += int(date_counts.iloc[end])
            end += 1
        core = dates[start:end]
        extended = dates[start : min(end + lookahead_dates, len(dates))]
        windows.append((core, extended))
        start = end
    return windows


def _read_feature_window(path: Path, dates: Iterable[pd.Timestamp], exchange: object = None, chunksize: int = 250_000) -> pd.DataFrame:
    wanted = _feature_usecols(path)
    target_dates = _date_values(dates)
    frames: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=wanted, chunksize=chunksize, low_memory=False):
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
        chunk = chunk[chunk["date"].isin(target_dates)]
        chunk = filter_listing_exchange(chunk, exchange=exchange, column="exchange")
        if not chunk.empty:
            frames.append(chunk)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=wanted)


def _downcast_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.select_dtypes(include=["float64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="float")
    for col in out.select_dtypes(include=["int64"]).columns:
        out[col] = pd.to_numeric(out[col], errors="coerce", downcast="integer")
    for col in ["ticker", "exchange", "sector", "industry", "country", "currency", "sentiment_status", "sentiment_source"]:
        if col in out.columns:
            out[col] = out[col].astype("category")
    return out


def _add_sentiment_features(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.sort_values(["ticker", "date"]).copy()
    for col in ["article_count", "sentiment_score_mean"]:
        if col not in out.columns:
            out[col] = 0 if col == "article_count" else pd.NA
    out["article_count"] = pd.to_numeric(out["article_count"], errors="coerce").fillna(0)
    out["sentiment_score_mean"] = pd.to_numeric(out["sentiment_score_mean"], errors="coerce")
    group = out.groupby("ticker", group_keys=False)
    out["sentiment_momentum_3d"] = group["sentiment_score_mean"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    out["sentiment_momentum_7d"] = group["sentiment_score_mean"].transform(lambda s: s.rolling(7, min_periods=1).mean())
    article_avg = group["article_count"].transform(lambda s: s.rolling(20, min_periods=3).mean())
    out["sentiment_volume_spike_flag"] = out["article_count"] > (article_avg.fillna(0) * 2)
    out["news_attention_score"] = out.groupby("date")["article_count"].rank(pct=True).fillna(0)
    return out


def _add_selection_scores(gold: pd.DataFrame) -> pd.DataFrame:
    out = gold.copy()
    for col in ["return_20d", "sma_gap_20_50", "sector_relative_momentum_score", "volume_confirmation_score", "liquidity_score"]:
        if col not in out.columns:
            out[col] = pd.NA
    out["data_quality_score"] = (1 - out.get("feature_missing_ratio", 0).fillna(0)).clip(0, 1)
    history_count = out.groupby("ticker")["date"].transform("size")
    out["history_quality_score"] = (history_count / 252).clip(upper=1)
    if "momentum_score" not in out.columns:
        out["momentum_score"] = out.groupby("date")["return_20d"].rank(pct=True).fillna(0.5)
    if "technical_setup_score" not in out.columns:
        out["technical_setup_score"] = out.groupby("date")["sma_gap_20_50"].rank(pct=True).fillna(0.5)
    out["sentiment_score"] = ((out["sentiment_score_mean"].fillna(0) + 1) / 2).clip(0, 1)
    score_cols = [
        "data_quality_score", "history_quality_score", "liquidity_score", "momentum_score",
        "sector_relative_momentum_score", "volume_confirmation_score", "sentiment_score", "technical_setup_score",
    ]
    for col in score_cols:
        if col not in out.columns:
            out[col] = 0.5
    out["selection_score"] = out[score_cols].astype(float).mean(axis=1)
    out["candidate_rank_overall"] = out.groupby("date")["selection_score"].rank(ascending=False, method="first")
    out["candidate_rank_by_sector"] = out.groupby(["date", "sector"])["selection_score"].rank(ascending=False, method="first")
    return out


def build_gold_dataset_from_frames(features: pd.DataFrame, sentiment: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    out = _downcast_frame(features)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()

    if sentiment is not None and not sentiment.empty:
        sent = sentiment.copy()
        sent["date"] = pd.to_datetime(sent["date"], errors="coerce")
        sent["ticker"] = sent["ticker"].astype(str).str.upper().str.strip()
        sent = sent.dropna(subset=["date", "ticker"])
        sent = sent.drop_duplicates(["date", "ticker"], keep="last")
        sent = _downcast_frame(sent)
        out = out.merge(sent, on=["date", "ticker"], how="left", suffixes=("", "_sentiment"))

    sentiment_defaults = {
        "article_count": 0,
        "sentiment_positive_count": 0,
        "sentiment_negative_count": 0,
        "sentiment_neutral_count": 0,
        "sentiment_status": "unavailable",
        "sentiment_source": "none",
    }
    for col, default in sentiment_defaults.items():
        if col not in out.columns:
            out[col] = default
        else:
            out[col] = out[col].fillna(default)

    out = _add_sentiment_features(out)
    out = _add_selection_scores(out)
    out = add_ranking_targets(out)
    for col in GOLD_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return _downcast_frame(out[GOLD_COLUMNS]).sort_values(["date", "ticker"]).reset_index(drop=True)


def build_portal_outputs(gold: pd.DataFrame, stamp: str) -> Dict[str, Path]:
    latest_date = gold["date"].max()
    today = gold[gold["date"].eq(latest_date)].copy()
    today["trade_action"] = today["target_trade_label_5d"].replace({"Neutral": "No Decision"})
    signals = today.sort_values("selection_score", ascending=False).head(100)[
        ["date", "ticker", "company", "sector", "selection_score", "candidate_rank_overall", "trade_action"]
    ]
    signals_path = PORTAL_OUTPUTS_DIR / f"07_portal_signals_{stamp}.csv"
    signals.to_csv(signals_path, index=False)

    dashboard = pd.DataFrame(
        [{
            "as_of_date": latest_date,
            "total_rows": len(gold),
            "ticker_count": gold["ticker"].nunique(),
            "feature_count": len([c for c in gold.columns if not c.startswith("target_")]),
            "long_candidates": int((today["target_trade_label_5d"] == "Long").sum()),
            "short_candidates": int((today["target_trade_label_5d"] == "Short").sum()),
            "no_decision_candidates": int((today["target_trade_label_5d"] == "Neutral").sum()),
        }]
    )
    dashboard_path = PORTAL_OUTPUTS_DIR / f"07_portal_dashboard_metrics_{stamp}.csv"
    dashboard.to_csv(dashboard_path, index=False)

    sector = today.groupby("sector", as_index=False).agg(
        ticker_count=("ticker", "size"),
        avg_selection_score=("selection_score", "mean"),
        avg_relative_strength=("sector_relative_strength_score", "mean"),
    )
    sector_path = PORTAL_OUTPUTS_DIR / f"07_portal_sector_breakdown_{stamp}.csv"
    sector.to_csv(sector_path, index=False)

    return {"portal_signals": signals_path, "portal_dashboard_metrics": dashboard_path, "portal_sector_breakdown": sector_path}


def _build_gold_quality_from_file(path: Path) -> pd.DataFrame:
    usecols = ["date", "ticker", "target_trade_label_5d"]
    rows = 0
    tickers: set[str] = set()
    min_date = pd.NaT
    max_date = pd.NaT
    long_count = 0
    short_count = 0
    neutral_count = 0
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=250_000, low_memory=False):
        rows += len(chunk)
        tickers.update(chunk["ticker"].dropna().astype(str).unique())
        dates = pd.to_datetime(chunk["date"], errors="coerce")
        current_min = dates.min()
        current_max = dates.max()
        min_date = current_min if pd.isna(min_date) or current_min < min_date else min_date
        max_date = current_max if pd.isna(max_date) or current_max > max_date else max_date
        labels = chunk["target_trade_label_5d"].astype(str)
        long_count += int(labels.eq("Long").sum())
        short_count += int(labels.eq("Short").sum())
        neutral_count += int(labels.eq("Neutral").sum())
    return pd.DataFrame(
        [
            {
                "row_count": rows,
                "ticker_count": len(tickers),
                "min_date": min_date,
                "max_date": max_date,
                "long_count": long_count,
                "short_count": short_count,
                "neutral_count": neutral_count,
                "leakage_column_count": len([col for col in GOLD_COLUMNS if col.startswith("target_")]),
            }
        ]
    )


def _build_data_dictionary_from_columns() -> pd.DataFrame:
    sample = pd.DataFrame(columns=GOLD_COLUMNS)
    return build_data_dictionary(sample)


def _build_portal_outputs_from_file(path: Path, stamp: str) -> Dict[str, Path]:
    latest_date = None
    latest_rows: list[pd.DataFrame] = []
    usecols = [
        "date",
        "ticker",
        "company",
        "sector",
        "selection_score",
        "candidate_rank_overall",
        "target_trade_label_5d",
        "sector_relative_strength_score",
    ]
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=250_000, low_memory=False):
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
        current_latest = chunk["date"].max()
        if latest_date is None or current_latest > latest_date:
            latest_date = current_latest
            latest_rows = [chunk[chunk["date"].eq(latest_date)].copy()]
        elif current_latest == latest_date:
            latest_rows.append(chunk[chunk["date"].eq(latest_date)].copy())
    latest = pd.concat(latest_rows, ignore_index=True) if latest_rows else pd.DataFrame(columns=usecols)
    return build_portal_outputs(latest, stamp)


def _write_gold_dataset_sharded(
    feature_path: Path,
    *,
    stamp: str,
    exchange: object = None,
    shard_rows: int,
) -> tuple[Path, int]:
    gold_path = GOLD_DIR / f"06_us_gold_ml_dataset_{stamp}.csv"
    if gold_path.exists():
        gold_path.unlink()
    counts = _feature_date_counts(feature_path, exchange=exchange)
    windows = _date_windows(counts, max_rows=shard_rows)
    total_rows = 0
    for index, (core_dates, extended_dates) in enumerate(windows, start=1):
        log(f"Building Gold shard {index}/{len(windows)} dates={len(core_dates)} rows~{int(counts.loc[core_dates].sum())}")
        features = _read_feature_window(feature_path, extended_dates, exchange=exchange)
        gold = build_gold_dataset_from_frames(features, sentiment=None)
        core_set = _date_values(core_dates)
        gold = gold[pd.to_datetime(gold["date"], errors="coerce").isin(core_set)].copy()
        gold.to_csv(gold_path, index=False, mode="a", header=not gold_path.exists())
        total_rows += len(gold)
        del features
        del gold
    return gold_path, total_rows


def _filter_features_exchange(features: pd.DataFrame, exchange: object = None) -> pd.DataFrame:
    return filter_listing_exchange(features, exchange=exchange, column="exchange")


def build_gold_dataset(
    limit_tickers: Optional[int] = None,
    exchange: object = None,
    feature_file: Optional[Path] = None,
    sentiment_file: Optional[Path] = None,
    skip_sentiment: bool = False,
    shard_rows: Optional[int] = None,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    feature_path = feature_file or latest_feature_panel_file()
    if shard_rows and not limit_tickers and skip_sentiment:
        gold_path, row_count = _write_gold_dataset_sharded(
            feature_path,
            stamp=stamp,
            exchange=exchange,
            shard_rows=shard_rows,
        )
        log(f"Wrote Gold ML dataset: {gold_path} ({row_count:,} rows)")

        quality_path = INTERIM_DIR / f"06_us_gold_quality_{stamp}.csv"
        _build_gold_quality_from_file(gold_path).to_csv(quality_path, index=False)
        log(f"Wrote Gold quality: {quality_path}")

        dictionary_path = INTERIM_DIR / f"06_us_gold_data_dictionary_{stamp}.csv"
        _build_data_dictionary_from_columns().to_csv(dictionary_path, index=False)
        log(f"Wrote Gold data dictionary: {dictionary_path}")

        paths = {"gold_dataset": gold_path, "gold_quality": quality_path, "gold_data_dictionary": dictionary_path}
        paths.update(_build_portal_outputs_from_file(gold_path, stamp))
        return paths

    features = _read_feature_panel(feature_path)
    features = _filter_features_exchange(features, exchange)
    if limit_tickers:
        tickers = features["ticker"].astype(str).str.upper().drop_duplicates().head(limit_tickers).tolist()
        features = features[features["ticker"].astype(str).str.upper().isin(tickers)].copy()
    sentiment_path = None if skip_sentiment else sentiment_file or latest_sentiment_panel_file()
    sentiment = pd.read_csv(sentiment_path, low_memory=False) if sentiment_path else None
    gold = build_gold_dataset_from_frames(features, sentiment)

    gold_path = GOLD_DIR / f"06_us_gold_ml_dataset_{stamp}.csv"
    gold.to_csv(gold_path, index=False)
    log(f"Wrote Gold ML dataset: {gold_path} ({len(gold):,} rows)")

    quality_path = INTERIM_DIR / f"06_us_gold_quality_{stamp}.csv"
    build_gold_quality(gold).to_csv(quality_path, index=False)
    log(f"Wrote Gold quality: {quality_path}")

    dictionary_path = INTERIM_DIR / f"06_us_gold_data_dictionary_{stamp}.csv"
    build_data_dictionary(gold).to_csv(dictionary_path, index=False)
    log(f"Wrote Gold data dictionary: {dictionary_path}")

    paths = {"gold_dataset": gold_path, "gold_quality": quality_path, "gold_data_dictionary": dictionary_path}
    paths.update(build_portal_outputs(gold, stamp))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--exchange", default=None, help="Optional listing exchange filter, e.g. NYSE")
    parser.add_argument("--feature-file", type=Path, default=None, help="Optional feature panel CSV to use instead of latest.")
    parser.add_argument("--sentiment-file", type=Path, default=None, help="Optional sentiment panel CSV to use instead of latest.")
    parser.add_argument("--skip-sentiment", action="store_true", help="Build gold without merging the sentiment panel.")
    parser.add_argument("--shard-rows", type=int, default=None, help="Approximate feature rows per date-window shard. Requires --skip-sentiment.")
    args = parser.parse_args()
    paths = build_gold_dataset(
        limit_tickers=args.limit_tickers,
        exchange=args.exchange,
        feature_file=args.feature_file,
        sentiment_file=args.sentiment_file,
        skip_sentiment=args.skip_sentiment,
        shard_rows=args.shard_rows,
    )
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
