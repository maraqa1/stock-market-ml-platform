from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
from pandas.errors import EmptyDataError

from stockml.common.paths import (
    GOLD_DIR,
    INTERIM_DIR,
    MODEL_OUTPUTS_DIR,
    PORTAL_OUTPUTS_DIR,
    PROCESSED_DIR,
    PROJECT_ROOT,
    RAW_DIR,
    ensure_data_dirs,
    latest_file,
    timestamp,
)


LINEAGE_COLUMNS = [
    "symbol",
    "lineage_scope",
    "raw_universe_seen",
    "raw_source",
    "raw_listing_exchange",
    "raw_security_name",
    "raw_financial_status",
    "raw_etf_flag",
    "cleaned_seen",
    "exclude_reason",
    "tradable_seen",
    "price_validated_seen",
    "price_quality_status",
    "price_min_date",
    "price_max_date",
    "price_latest_close",
    "price_avg_dollar_volume_20d",
    "metadata_seen",
    "company",
    "exchange",
    "sector",
    "industry",
    "market_cap",
    "country",
    "currency",
    "metadata_status",
    "feature_seen",
    "feature_latest_date",
    "feature_return_5d",
    "feature_return_20d",
    "feature_rsi_14",
    "feature_volatility_20d",
    "sentiment_seen",
    "sentiment_latest_date",
    "sentiment_article_count",
    "sentiment_score_mean",
    "sentiment_status",
    "gold_seen",
    "gold_latest_date",
    "gold_candidate_rank_overall",
    "gold_selection_score",
    "gold_target_trade_label_5d",
    "model_seen",
    "model_signal_date",
    "model_rank_overall",
    "model_candidate_rank_overall",
    "model_trade_action",
    "model_meta_label_decision",
    "model_risk_adjusted_score",
    "candidate_seen",
    "candidate_trade_action",
    "candidate_meta_label_decision",
    "candidate_trade_quality_status",
    "candidate_status",
    "candidate_order_eligible",
    "candidate_risk_adjusted_score",
    "order_plan_seen",
    "order_side",
    "order_type",
    "order_extended_hours",
    "order_limit_price",
    "order_approved_notional",
    "order_suggested_quantity",
    "order_trade_quality_status",
    "order_client_order_id",
    "order_result_seen",
    "order_result_status",
    "order_result_alpaca_status",
    "tracking_seen",
    "tracking_alpaca_status",
    "tracking_filled_qty",
    "tracking_filled_avg_price",
    "position_seen",
    "position_side",
    "position_qty",
    "position_market_value",
    "position_unrealized_pl",
    "lineage_sources",
    "lineage_warnings",
]


def _latest(base: Path, subdir: str, pattern: str) -> Path | None:
    return latest_file(base / "data" / subdir, pattern)


def _read_csv(path: Path | None, *, usecols: Iterable[str] | None = None) -> pd.DataFrame:
    if not path or not path.exists() or path.stat().st_size <= 1:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, usecols=(lambda column: column in set(usecols)) if usecols else None, low_memory=False)
    except (EmptyDataError, ValueError):
        return pd.DataFrame()


def _symbol_column(frame: pd.DataFrame) -> str:
    if "symbol" in frame.columns:
        return "symbol"
    if "ticker" in frame.columns:
        return "ticker"
    return ""


def _normalize_symbols(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    symbol_col = _symbol_column(frame)
    if not symbol_col:
        return pd.DataFrame()
    out = frame.copy()
    out["symbol"] = out[symbol_col].astype(str).str.strip().str.upper()
    out = out[out["symbol"].ne("") & out["symbol"].ne("NAN")]
    return out


def _latest_by_symbol(frame: pd.DataFrame, date_column: str | None = None) -> pd.DataFrame:
    frame = _normalize_symbols(frame)
    if frame.empty:
        return frame
    if date_column and date_column in frame.columns:
        out = frame.copy()
        out["__lineage_date"] = pd.to_datetime(out[date_column], errors="coerce", utc=True)
        out = out.sort_values(["symbol", "__lineage_date"]).drop_duplicates("symbol", keep="last")
        return out.drop(columns=["__lineage_date"])
    return frame.drop_duplicates("symbol", keep="last")


def _as_lookup(frame: pd.DataFrame, date_column: str | None = None) -> dict[str, dict[str, object]]:
    latest = _latest_by_symbol(frame, date_column)
    if latest.empty:
        return {}
    return {str(row["symbol"]): row for row in latest.to_dict("records")}


def _value(row: dict[str, object] | None, key: str, default: object = pd.NA) -> object:
    if not row:
        return default
    value = row.get(key, default)
    return default if value is None else value


def _truth(row: dict[str, object] | None) -> bool:
    return bool(row)


def _artifact_path(path: Path | None) -> str:
    return str(path) if path else ""


def build_ticker_lineage(
    *,
    root: Path | None = None,
    symbols: Iterable[str] | None = None,
    stamp: str | None = None,
) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    ensure_data_dirs()
    run_stamp = stamp or timestamp()

    paths = {
        "raw_universe": _latest(base, "raw", "01_us_equity_universe_*.csv"),
        "cleaned_universe": _latest(base, "interim", "02_us_universe_cleaned_*.csv"),
        "tradable_universe": _latest(base, "interim", "02_us_tradable_universe_*.csv"),
        "price_validated": _latest(base, "interim", "03_us_price_validated_universe_*.csv"),
        "metadata": _latest(base, "interim", "04_us_metadata_enriched_*.csv"),
        "features": _latest(base, "processed", "05_us_feature_panel_*.csv"),
        "sentiment": base / "data" / "processed" / "05_news_sentiment_store.csv",
        "gold": _latest(base, "gold", "06_us_gold_ml_dataset_*.csv"),
        "model": _latest(base, "model_outputs", "advanced_model_signal_table_*.csv"),
        "candidate_pool": _latest(base, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"),
        "order_plan": _latest(base, "portal_outputs", "08_alpaca_paper_order_plan_*.csv"),
        "order_results": _latest(base, "portal_outputs", "08_alpaca_paper_order_results_*.csv"),
        "tracking": _latest(base, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv"),
        "positions": _latest(base, "portal_outputs", "08_alpaca_paper_positions_*.csv"),
    }

    raw = _read_csv(paths["raw_universe"], usecols=["symbol", "source", "listing_exchange", "security_name", "financial_status", "etf_flag"])
    cleaned = _read_csv(paths["cleaned_universe"], usecols=["symbol", "exclude_reason", "is_tradable_common_stock_candidate"])
    tradable = _read_csv(paths["tradable_universe"], usecols=["symbol", "exclude_reason", "is_tradable_common_stock_candidate"])
    price = _read_csv(
        paths["price_validated"],
        usecols=["symbol", "ticker", "price_quality_status", "min_date", "max_date", "latest_close", "avg_dollar_volume_20d"],
    )
    metadata = _read_csv(
        paths["metadata"],
        usecols=["ticker", "company", "exchange", "sector", "industry", "market_cap", "country", "currency", "metadata_status"],
    )
    features = _read_csv(
        paths["features"],
        usecols=["date", "ticker", "return_5d", "return_20d", "rsi_14", "volatility_20d"],
    )
    sentiment = _read_csv(
        paths["sentiment"],
        usecols=["date", "ticker", "article_count", "sentiment_score_mean", "sentiment_status"],
    )
    gold = _read_csv(
        paths["gold"],
        usecols=["date", "ticker", "candidate_rank_overall", "selection_score", "target_trade_label_5d"],
    )
    model = _read_csv(
        paths["model"],
        usecols=[
            "date",
            "ticker",
            "rank_overall",
            "candidate_rank_overall",
            "trade_action",
            "meta_label_decision",
            "risk_adjusted_score",
        ],
    )
    candidate = _read_csv(
        paths["candidate_pool"],
        usecols=[
            "symbol",
            "company",
            "sector",
            "trade_action",
            "meta_label_decision",
            "trade_quality_status",
            "candidate_status",
            "order_eligible",
            "risk_adjusted_score",
        ],
    )
    plan = _read_csv(
        paths["order_plan"],
        usecols=[
            "symbol",
            "side",
            "type",
            "extended_hours",
            "limit_price",
            "approved_notional",
            "suggested_quantity",
            "trade_quality_status",
            "client_order_id",
        ],
    )
    results = _read_csv(paths["order_results"], usecols=["symbol", "status", "alpaca_status"])
    tracking = _read_csv(paths["tracking"], usecols=["symbol", "alpaca_status", "filled_qty", "filled_avg_price"])
    positions = _read_csv(paths["positions"], usecols=["symbol", "side", "qty", "market_value", "unrealized_pl"])

    lookups = {
        "raw": _as_lookup(raw),
        "cleaned": _as_lookup(cleaned),
        "tradable": _as_lookup(tradable),
        "price": _as_lookup(price),
        "metadata": _as_lookup(metadata),
        "features": _as_lookup(features, "date"),
        "sentiment": _as_lookup(sentiment, "date"),
        "gold": _as_lookup(gold, "date"),
        "model": _as_lookup(model, "date"),
        "candidate": _as_lookup(candidate),
        "plan": _as_lookup(plan),
        "results": _as_lookup(results),
        "tracking": _as_lookup(tracking),
        "positions": _as_lookup(positions),
    }

    requested = {str(symbol).strip().upper() for symbol in symbols or [] if str(symbol).strip()}
    symbol_set = set(requested)
    for stage in ["candidate", "plan", "results", "tracking", "positions"]:
        symbol_set.update(lookups[stage])
    if not symbol_set:
        symbol_set.update(list(lookups["model"])[:200])

    rows = []
    for symbol in sorted(symbol_set):
        raw_row = lookups["raw"].get(symbol)
        cleaned_row = lookups["cleaned"].get(symbol)
        tradable_row = lookups["tradable"].get(symbol)
        price_row = lookups["price"].get(symbol)
        metadata_row = lookups["metadata"].get(symbol)
        feature_row = lookups["features"].get(symbol)
        sentiment_row = lookups["sentiment"].get(symbol)
        gold_row = lookups["gold"].get(symbol)
        model_row = lookups["model"].get(symbol)
        candidate_row = lookups["candidate"].get(symbol)
        plan_row = lookups["plan"].get(symbol)
        result_row = lookups["results"].get(symbol)
        tracking_row = lookups["tracking"].get(symbol)
        position_row = lookups["positions"].get(symbol)
        warnings = []
        if candidate_row and not model_row:
            warnings.append("candidate_without_model_row")
        if plan_row and not candidate_row:
            warnings.append("order_plan_without_candidate_pool_row")
        if tracking_row and not plan_row:
            warnings.append("tracking_without_order_plan_row")
        if not metadata_row:
            warnings.append("metadata_missing")
        row = {
            "symbol": symbol,
            "lineage_scope": "requested" if symbol in requested else "candidate_or_order",
            "raw_universe_seen": _truth(raw_row),
            "raw_source": _value(raw_row, "source"),
            "raw_listing_exchange": _value(raw_row, "listing_exchange"),
            "raw_security_name": _value(raw_row, "security_name"),
            "raw_financial_status": _value(raw_row, "financial_status"),
            "raw_etf_flag": _value(raw_row, "etf_flag"),
            "cleaned_seen": _truth(cleaned_row),
            "exclude_reason": _value(cleaned_row, "exclude_reason"),
            "tradable_seen": _truth(tradable_row),
            "price_validated_seen": _truth(price_row),
            "price_quality_status": _value(price_row, "price_quality_status"),
            "price_min_date": _value(price_row, "min_date"),
            "price_max_date": _value(price_row, "max_date"),
            "price_latest_close": _value(price_row, "latest_close"),
            "price_avg_dollar_volume_20d": _value(price_row, "avg_dollar_volume_20d"),
            "metadata_seen": _truth(metadata_row),
            "company": _value(metadata_row, "company", _value(candidate_row, "company")),
            "exchange": _value(metadata_row, "exchange"),
            "sector": _value(metadata_row, "sector", _value(candidate_row, "sector")),
            "industry": _value(metadata_row, "industry"),
            "market_cap": _value(metadata_row, "market_cap"),
            "country": _value(metadata_row, "country"),
            "currency": _value(metadata_row, "currency"),
            "metadata_status": _value(metadata_row, "metadata_status"),
            "feature_seen": _truth(feature_row),
            "feature_latest_date": _value(feature_row, "date"),
            "feature_return_5d": _value(feature_row, "return_5d"),
            "feature_return_20d": _value(feature_row, "return_20d"),
            "feature_rsi_14": _value(feature_row, "rsi_14"),
            "feature_volatility_20d": _value(feature_row, "volatility_20d"),
            "sentiment_seen": _truth(sentiment_row),
            "sentiment_latest_date": _value(sentiment_row, "date"),
            "sentiment_article_count": _value(sentiment_row, "article_count"),
            "sentiment_score_mean": _value(sentiment_row, "sentiment_score_mean"),
            "sentiment_status": _value(sentiment_row, "sentiment_status"),
            "gold_seen": _truth(gold_row),
            "gold_latest_date": _value(gold_row, "date"),
            "gold_candidate_rank_overall": _value(gold_row, "candidate_rank_overall"),
            "gold_selection_score": _value(gold_row, "selection_score"),
            "gold_target_trade_label_5d": _value(gold_row, "target_trade_label_5d"),
            "model_seen": _truth(model_row),
            "model_signal_date": _value(model_row, "date"),
            "model_rank_overall": _value(model_row, "rank_overall"),
            "model_candidate_rank_overall": _value(model_row, "candidate_rank_overall"),
            "model_trade_action": _value(model_row, "trade_action"),
            "model_meta_label_decision": _value(model_row, "meta_label_decision"),
            "model_risk_adjusted_score": _value(model_row, "risk_adjusted_score"),
            "candidate_seen": _truth(candidate_row),
            "candidate_trade_action": _value(candidate_row, "trade_action"),
            "candidate_meta_label_decision": _value(candidate_row, "meta_label_decision"),
            "candidate_trade_quality_status": _value(candidate_row, "trade_quality_status"),
            "candidate_status": _value(candidate_row, "candidate_status"),
            "candidate_order_eligible": _value(candidate_row, "order_eligible"),
            "candidate_risk_adjusted_score": _value(candidate_row, "risk_adjusted_score"),
            "order_plan_seen": _truth(plan_row),
            "order_side": _value(plan_row, "side"),
            "order_type": _value(plan_row, "type"),
            "order_extended_hours": _value(plan_row, "extended_hours"),
            "order_limit_price": _value(plan_row, "limit_price"),
            "order_approved_notional": _value(plan_row, "approved_notional"),
            "order_suggested_quantity": _value(plan_row, "suggested_quantity"),
            "order_trade_quality_status": _value(plan_row, "trade_quality_status"),
            "order_client_order_id": _value(plan_row, "client_order_id"),
            "order_result_seen": _truth(result_row),
            "order_result_status": _value(result_row, "status"),
            "order_result_alpaca_status": _value(result_row, "alpaca_status"),
            "tracking_seen": _truth(tracking_row),
            "tracking_alpaca_status": _value(tracking_row, "alpaca_status"),
            "tracking_filled_qty": _value(tracking_row, "filled_qty"),
            "tracking_filled_avg_price": _value(tracking_row, "filled_avg_price"),
            "position_seen": _truth(position_row),
            "position_side": _value(position_row, "side"),
            "position_qty": _value(position_row, "qty"),
            "position_market_value": _value(position_row, "market_value"),
            "position_unrealized_pl": _value(position_row, "unrealized_pl"),
            "lineage_sources": ";".join(f"{key}={_artifact_path(path)}" for key, path in paths.items()),
            "lineage_warnings": ";".join(warnings),
        }
        rows.append(row)

    lineage = pd.DataFrame(rows, columns=LINEAGE_COLUMNS)
    output_path = base / "data" / "portal_outputs" / f"ticker_lineage_{run_stamp}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lineage.to_csv(output_path, index=False)
    return {
        "status": "ok",
        "rows": int(len(lineage)),
        "symbols": int(lineage["symbol"].nunique()) if not lineage.empty else 0,
        "path": str(output_path),
        "warnings": int(lineage["lineage_warnings"].astype(str).ne("").sum()) if not lineage.empty else 0,
        "sources": {key: _artifact_path(value) for key, value in paths.items()},
    }
