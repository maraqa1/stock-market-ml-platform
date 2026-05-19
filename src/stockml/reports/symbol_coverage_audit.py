from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
from pandas.errors import EmptyDataError

from stockml.common.paths import PROJECT_ROOT, ensure_data_dirs, timestamp


AUDIT_COLUMNS = [
    "symbol",
    "exchange",
    "in_universe",
    "has_price",
    "price_rows",
    "price_latest_date",
    "price_source",
    "in_validated_universe",
    "has_metadata",
    "metadata_status",
    "metadata_error",
    "has_gold_rows",
    "gold_rows",
    "gold_latest_date",
    "has_model_prediction",
    "trade_action",
    "signal",
    "model_score",
    "rank_overall",
    "meta_label_probability",
    "meta_label_decision",
    "has_candidate_pool",
    "candidate_rank",
    "candidate_status",
    "has_order_plan",
    "order_status",
    "drop_stage",
    "drop_reason",
]


def _latest(directory: Path, pattern: str) -> Path | None:
    files = sorted(
        [path for path in directory.glob(pattern) if "summary" not in path.name],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _read_columns(path: Path | None, columns: Iterable[str]) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame()
    try:
        header = pd.read_csv(path, nrows=0)
    except EmptyDataError:
        return pd.DataFrame()
    wanted = [column for column in columns if column in header.columns]
    if not wanted:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, usecols=wanted, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame()


def _symbol_column(frame: pd.DataFrame) -> str | None:
    for column in ["symbol", "ticker", "yahoo_ticker"]:
        if column in frame.columns:
            return column
    return None


def _normalize_symbol_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    column = _symbol_column(frame)
    if not column:
        return pd.DataFrame()
    out = frame.copy()
    out["symbol"] = out[column].astype(str).str.upper().str.strip()
    out = out[out["symbol"].ne("")]
    return out


def _optional_str(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _drop_stage(row: pd.Series) -> tuple[str, str]:
    checks = [
        ("universe", "missing_from_universe", not row["in_universe"]),
        ("price", "missing_provider_price_history", not row["has_price"]),
        ("price_validation", "failed_or_missing_price_quality", not row["in_validated_universe"]),
        ("metadata", "missing_metadata", not row["has_metadata"]),
        ("gold", "missing_gold_rows", not row["has_gold_rows"]),
        ("model", "missing_model_prediction", not row["has_model_prediction"]),
        ("candidate_pool", "not_selected_for_candidate_pool", not row["has_candidate_pool"]),
    ]
    for stage, reason, failed in checks:
        if failed:
            return stage, reason
    if not row["has_order_plan"]:
        return "order_plan", "not_in_latest_order_plan"
    return "complete", "available_for_selection_review"


def build_symbol_coverage_audit(
    root: Path | None = None,
    symbols: Iterable[str] | None = None,
    provider_name: str | None = None,
    stamp: str | None = None,
) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    data = base / "data"
    interim = data / "interim"
    raw = data / "raw"
    gold_dir = data / "gold"
    model_dir = data / "model_outputs"
    portal_dir = data / "portal_outputs"
    ensure_data_dirs()

    universe_path = _latest(interim, "02_us_tradable_universe_*.csv")
    validated_path = _latest(interim, "03_us_price_validated_universe_*.csv")
    metadata_path = _latest(interim, "04_us_metadata_enriched_*.csv")
    gold_path = _latest(gold_dir, "06_us_gold_ml_dataset_*.csv")
    model_path = model_dir / "model_predictions_latest.csv"
    if not model_path.exists():
        model_path = _latest(model_dir, "advanced_model_latest_predictions_*.csv")
    pool_path = _latest(portal_dir, "08_alpaca_paper_candidate_pool_*.csv")
    plan_path = _latest(portal_dir, "08_alpaca_paper_order_plan_*.csv")
    price_path = raw / "03_us_price_history_store.csv"

    universe = _normalize_symbol_frame(
        _read_columns(universe_path, ["symbol", "ticker", "yahoo_ticker", "exchange", "listing_exchange", "company"])
    )
    price = _normalize_symbol_frame(_read_columns(price_path, ["ticker", "date", "source"]))
    if provider_name and not price.empty and "source" in price.columns:
        price = price[price["source"].astype(str).str.strip().eq(str(provider_name).strip())].copy()
    validated = _normalize_symbol_frame(_read_columns(validated_path, ["symbol", "ticker", "yahoo_ticker"]))
    metadata = _normalize_symbol_frame(
        _read_columns(metadata_path, ["symbol", "ticker", "sector", "market_cap", "metadata_status", "metadata_error"])
    )
    gold = _normalize_symbol_frame(_read_columns(gold_path, ["ticker", "symbol", "date"]))
    model = _normalize_symbol_frame(
        _read_columns(
            model_path,
            [
                "ticker",
                "symbol",
                "trade_action",
                "signal",
                "model_score",
                "rank_overall",
                "meta_label_probability",
                "meta_label_decision",
            ],
        )
    )
    pool = _normalize_symbol_frame(_read_columns(pool_path, ["symbol", "ticker", "candidate_rank", "candidate_status"]))
    plan = _normalize_symbol_frame(_read_columns(plan_path, ["symbol", "ticker", "status"]))

    requested = {str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()}
    all_symbols = set(requested)
    for frame in [universe, price, validated, metadata, gold, model, pool, plan]:
        if not frame.empty and "symbol" in frame.columns:
            all_symbols.update(frame["symbol"].dropna().astype(str))
    if requested:
        all_symbols = all_symbols.intersection(requested) or requested

    universe_index = universe.drop_duplicates("symbol").set_index("symbol") if not universe.empty else pd.DataFrame()
    validated_symbols = set(validated["symbol"]) if not validated.empty else set()
    metadata_index = metadata.drop_duplicates("symbol").set_index("symbol") if not metadata.empty else pd.DataFrame()
    model_index = model.drop_duplicates("symbol").set_index("symbol") if not model.empty else pd.DataFrame()
    pool_index = pool.drop_duplicates("symbol").set_index("symbol") if not pool.empty else pd.DataFrame()
    plan_index = plan.drop_duplicates("symbol").set_index("symbol") if not plan.empty else pd.DataFrame()

    if not price.empty:
        if "source" not in price.columns:
            price["source"] = ""
        if "date" not in price.columns:
            price["date"] = ""
        price_summary = (
            price.sort_values("date")
            .groupby("symbol")
            .agg(price_rows=("symbol", "size"), price_latest_date=("date", "last"), price_source=("source", "last"))
        )
    else:
        price_summary = pd.DataFrame()

    if not gold.empty:
        if "date" not in gold.columns:
            gold["date"] = ""
        gold_summary = gold.sort_values("date").groupby("symbol").agg(gold_rows=("symbol", "size"), gold_latest_date=("date", "last"))
    else:
        gold_summary = pd.DataFrame()

    rows: list[dict[str, object]] = []
    for symbol in sorted(all_symbols):
        universe_row = universe_index.loc[symbol] if symbol in universe_index.index else pd.Series(dtype=object)
        price_row = price_summary.loc[symbol] if symbol in price_summary.index else pd.Series(dtype=object)
        metadata_row = metadata_index.loc[symbol] if symbol in metadata_index.index else pd.Series(dtype=object)
        gold_row = gold_summary.loc[symbol] if symbol in gold_summary.index else pd.Series(dtype=object)
        model_row = model_index.loc[symbol] if symbol in model_index.index else pd.Series(dtype=object)
        pool_row = pool_index.loc[symbol] if symbol in pool_index.index else pd.Series(dtype=object)
        plan_row = plan_index.loc[symbol] if symbol in plan_index.index else pd.Series(dtype=object)

        exchange = universe_row.get("listing_exchange", universe_row.get("exchange", ""))
        row = {
            "symbol": symbol,
            "exchange": _optional_str(exchange),
            "in_universe": symbol in universe_index.index,
            "has_price": symbol in price_summary.index,
            "price_rows": int(price_row.get("price_rows", 0) or 0),
            "price_latest_date": _optional_str(price_row.get("price_latest_date", "")),
            "price_source": _optional_str(price_row.get("price_source", "")),
            "in_validated_universe": symbol in validated_symbols,
            "has_metadata": symbol in metadata_index.index,
            "metadata_status": _optional_str(metadata_row.get("metadata_status", "")),
            "metadata_error": _optional_str(metadata_row.get("metadata_error", "")),
            "has_gold_rows": symbol in gold_summary.index,
            "gold_rows": int(gold_row.get("gold_rows", 0) or 0),
            "gold_latest_date": _optional_str(gold_row.get("gold_latest_date", "")),
            "has_model_prediction": symbol in model_index.index,
            "trade_action": _optional_str(model_row.get("trade_action", "")),
            "signal": _optional_str(model_row.get("signal", "")),
            "model_score": model_row.get("model_score", ""),
            "rank_overall": model_row.get("rank_overall", ""),
            "meta_label_probability": model_row.get("meta_label_probability", ""),
            "meta_label_decision": _optional_str(model_row.get("meta_label_decision", "")),
            "has_candidate_pool": symbol in pool_index.index,
            "candidate_rank": pool_row.get("candidate_rank", ""),
            "candidate_status": _optional_str(pool_row.get("candidate_status", "")),
            "has_order_plan": symbol in plan_index.index,
            "order_status": _optional_str(plan_row.get("status", "")),
        }
        row["drop_stage"], row["drop_reason"] = _drop_stage(pd.Series(row))
        rows.append(row)

    report = pd.DataFrame(rows, columns=AUDIT_COLUMNS)
    out_dir = interim
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"00_symbol_coverage_audit_{stamp or timestamp()}.csv"
    report.to_csv(out_path, index=False)
    return {
        "status": "ok",
        "rows": int(len(report)),
        "path": str(out_path),
        "provider": provider_name or "",
        "artifacts": {
            "universe": str(universe_path or ""),
            "price": str(price_path if price_path.exists() else ""),
            "validated": str(validated_path or ""),
            "metadata": str(metadata_path or ""),
            "gold": str(gold_path or ""),
            "model": str(model_path or ""),
            "candidate_pool": str(pool_path or ""),
            "order_plan": str(plan_path or ""),
        },
    }
