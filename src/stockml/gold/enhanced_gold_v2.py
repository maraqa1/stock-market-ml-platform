from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import GOLD_DIR, latest_file, timestamp
from stockml.gold.target_engineering import leakage_columns, model_feature_columns


DECISION_COLUMNS = [
    "date",
    "ticker",
    "exchange",
    "company",
    "sector",
    "industry",
    "country",
    "currency",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "adjusted_close",
    "volume",
    "dollar_volume",
    "avg_dollar_volume_20d",
    "return_1d",
    "return_5d",
    "return_10d",
    "return_20d",
    "return_60d",
    "sma_20",
    "sma_50",
    "sma_200",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "volatility_20d",
    "volatility_60d",
    "max_drawdown_60d",
    "forward_5d_return",
    "forward_10d_return",
    "forward_20d_return",
    "forward_5d_alpha_vs_sector",
    "forward_20d_alpha_vs_sector",
    "target_trade_label_5d",
    "target_trade_label_20d",
    "momentum_score",
    "relative_strength_score",
    "technical_entry_score",
    "risk_penalty",
    "missing_data_penalty",
    "data_completeness_score",
    "final_trade_score",
    "trade_confidence",
    "trade_decision",
    "decision_reason",
    "risk_warning",
]


FEATURE_FAMILIES = {
    "identity": {"date", "ticker", "exchange", "company", "sector", "industry", "country", "currency"},
    "price_liquidity": {"open", "high", "low", "close", "adj_close", "adjusted_close", "volume", "dollar_volume", "avg_dollar_volume_20d"},
    "momentum": {"return_1d", "return_5d", "return_10d", "return_20d", "return_60d"},
    "technical": {"sma_20", "sma_50", "sma_200", "rsi_14", "macd", "macd_signal", "macd_hist"},
    "risk": {"volatility_20d", "volatility_60d", "max_drawdown_60d", "risk_penalty"},
    "target": {
        "forward_5d_return",
        "forward_10d_return",
        "forward_20d_return",
        "forward_5d_alpha_vs_sector",
        "forward_20d_alpha_vs_sector",
        "target_trade_label_5d",
        "target_trade_label_20d",
    },
    "score": {
        "momentum_score",
        "relative_strength_score",
        "technical_entry_score",
        "missing_data_penalty",
        "data_completeness_score",
        "final_trade_score",
        "trade_confidence",
        "trade_decision",
        "decision_reason",
        "risk_warning",
    },
}


@dataclass(frozen=True)
class EnhancedGoldOutputs:
    decision_daily: Path
    candidates_latest: Path
    feature_catalog: Path
    data_quality_report: Path


def latest_gold_file() -> Path:
    path = latest_file(GOLD_DIR, "06_us_gold_ml_dataset_*.csv")
    if path is None:
        raise FileNotFoundError("No 06_us_gold_ml_dataset_*.csv file found.")
    return path


def _family(column: str) -> str:
    for family, columns in FEATURE_FAMILIES.items():
        if column in columns:
            return family
    if column.startswith(("target_", "forward_")):
        return "target"
    return "other"


def _pct_rank(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return frame.groupby("date")[column].rank(pct=True)


def _ensure_enhanced_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    if "adjusted_close" not in out.columns:
        out["adjusted_close"] = out.get("adj_close")
    out["forward_5d_return"] = out.get("target_return_5d")
    out["forward_10d_return"] = out.get("target_return_10d")
    out["forward_20d_return"] = out.get("target_return_20d", pd.NA)
    out["forward_5d_alpha_vs_sector"] = out.get("target_sector_relative_return_5d")
    out["forward_20d_alpha_vs_sector"] = out.get("target_sector_relative_return_20d", pd.NA)
    out["target_trade_label_5d"] = _trade_label(out["forward_5d_alpha_vs_sector"])
    out.loc[out["forward_5d_alpha_vs_sector"].isna(), "target_trade_label_5d"] = pd.NA
    out["target_trade_label_20d"] = _trade_label(out["forward_20d_alpha_vs_sector"])
    out.loc[out["forward_20d_alpha_vs_sector"].isna(), "target_trade_label_20d"] = pd.NA
    out["momentum_score"] = _pct_rank(out, "return_20d").fillna(_pct_rank(out, "return_5d"))
    out["relative_strength_score"] = _pct_rank(out, "target_sector_relative_return_5d").fillna(_pct_rank(out, "sector_relative_momentum_score"))
    out["technical_entry_score"] = _pct_rank(out, "technical_setup_score").fillna(_pct_rank(out, "rsi_14"))
    out["risk_penalty"] = (1.0 - _pct_rank(out, "volatility_20d")).fillna(0.5)
    required_inputs = ["close", "volume", "avg_dollar_volume_20d", "return_20d", "rsi_14", "volatility_20d"]
    present = [col for col in required_inputs if col in out.columns]
    out["data_completeness_score"] = out[present].notna().mean(axis=1) if present else 0.0
    out["missing_data_penalty"] = 1.0 - out["data_completeness_score"]
    score = (
        0.30 * pd.to_numeric(out["momentum_score"], errors="coerce").fillna(0.5)
        + 0.25 * pd.to_numeric(out["relative_strength_score"], errors="coerce").fillna(0.5)
        + 0.20 * pd.to_numeric(out["technical_entry_score"], errors="coerce").fillna(0.5)
        - 0.15 * pd.to_numeric(out["risk_penalty"], errors="coerce").fillna(0.5)
        - 0.10 * pd.to_numeric(out["missing_data_penalty"], errors="coerce").fillna(0.0)
    )
    out["final_trade_score"] = score.clip(0, 1)
    out["trade_confidence"] = (out["data_completeness_score"] * (1.0 - out["missing_data_penalty"])).clip(0, 1)
    out["trade_decision"] = _trade_decision(out)
    out["decision_reason"] = out["trade_decision"].map(
        {
            "Trade_Candidate": "score_threshold_passed",
            "Watchlist": "watchlist_score_band",
            "No_Trade": "score_below_trade_threshold",
            "Insufficient_Data": "data_completeness_below_threshold",
        }
    ).fillna("risk_or_quality_filter")
    out["risk_warning"] = ""
    for column in DECISION_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    return out


def _trade_label(alpha: pd.Series) -> pd.Series:
    values = pd.to_numeric(alpha, errors="coerce")
    label = pd.Series("Neutral", index=values.index, dtype="object")
    label[(values > 0.03)] = "Strong_Trade_Buy"
    label[(values >= 0.01) & (values <= 0.03)] = "Trade_Buy"
    label[(values >= -0.01) & (values < 0.01)] = "Neutral"
    label[(values >= -0.03) & (values < -0.01)] = "Avoid"
    label[(values < -0.03)] = "Weak"
    return label


def _trade_decision(frame: pd.DataFrame) -> pd.Series:
    score = pd.to_numeric(frame["final_trade_score"], errors="coerce").fillna(0)
    completeness = pd.to_numeric(frame["data_completeness_score"], errors="coerce").fillna(0)
    out = pd.Series("No_Trade", index=frame.index, dtype="object")
    out[score >= 0.75] = "Trade_Candidate"
    out[(score >= 0.60) & (score < 0.75)] = "Watchlist"
    out[completeness < 0.60] = "Insufficient_Data"
    return out


def build_feature_catalog(columns: list[str]) -> pd.DataFrame:
    leakage = set(leakage_columns(columns))
    model_inputs = set(model_feature_columns(columns))
    rows = []
    for column in columns:
        rows.append(
            {
                "column": column,
                "feature_family": _family(column),
                "source": "gold_v1_derived" if column in DECISION_COLUMNS else "gold_v1",
                "description": column.replace("_", " "),
                "allowed_model_input": column in model_inputs and column not in leakage,
                "is_target_or_leakage": column in leakage,
                "expected_dtype": "numeric" if column not in {"date", "ticker", "company", "sector", "industry", "exchange", "country", "currency"} else "string",
                "null_handling": "allowed_for_latest_forward_labels" if column.startswith(("forward_", "target_")) else "score_penalty_or_na",
            }
        )
    return pd.DataFrame(rows)


def build_quality_report(frame: pd.DataFrame, *, min_rows: int = 10, min_dates: int = 5) -> pd.DataFrame:
    duplicate_rate = frame.duplicated(["ticker", "date"]).mean() if {"ticker", "date"}.issubset(frame.columns) and len(frame) else 0.0
    label_counts = frame["target_trade_label_5d"].astype("string").value_counts(dropna=False).to_dict() if "target_trade_label_5d" in frame.columns else {}
    non_na_labels = {str(k): int(v) for k, v in label_counts.items() if str(k) not in {"<NA>", "nan", "None"}}
    all_neutral = bool(non_na_labels) and set(non_na_labels) == {"Neutral"}
    rows = [
        {"check": "row_count", "observed": len(frame), "status": "pass" if len(frame) >= min_rows else "fail", "message": f">={min_rows}"},
        {"check": "ticker_count", "observed": frame["ticker"].nunique() if "ticker" in frame else 0, "status": "info", "message": ""},
        {"check": "date_count", "observed": frame["date"].nunique() if "date" in frame else 0, "status": "pass" if frame.get("date", pd.Series(dtype=object)).nunique() >= min_dates else "fail", "message": f">={min_dates}"},
        {"check": "duplicate_ticker_date_count", "observed": int(frame.duplicated(["ticker", "date"]).sum()) if {"ticker", "date"}.issubset(frame.columns) else 0, "status": "pass" if duplicate_rate == 0 else "fail", "message": ""},
        {"check": "target_label_distribution", "observed": label_counts, "status": "fail" if all_neutral else "pass", "message": "non-NA labels must not be all Neutral"},
        {"check": "leakage_check", "observed": leakage_columns(list(frame.columns)), "status": "pass", "message": "target/forward columns excluded by catalog"},
        {"check": "score_range_check", "observed": _score_range(frame), "status": "pass" if _score_range_ok(frame) else "fail", "message": "scores must be in [0,1]"},
    ]
    for family, columns in FEATURE_FAMILIES.items():
        present = [col for col in columns if col in frame.columns]
        missing_rate = float(frame[present].isna().mean().mean()) if present and len(frame) else 1.0
        rows.append({"check": f"missing_rate_{family}", "observed": round(missing_rate, 6), "status": "info", "message": ",".join(present)})
    return pd.DataFrame(rows)


def _score_range(frame: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in ["momentum_score", "relative_strength_score", "technical_entry_score", "final_trade_score", "trade_confidence"]:
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            out[column] = {"min": values.min(skipna=True), "max": values.max(skipna=True)}
    return out


def _score_range_ok(frame: pd.DataFrame) -> bool:
    for value in _score_range(frame).values():
        if pd.notna(value["min"]) and float(value["min"]) < 0:
            return False
        if pd.notna(value["max"]) and float(value["max"]) > 1:
            return False
    return True


def build_enhanced_gold_v2(
    gold_file: Path | None = None,
    *,
    stamp: str | None = None,
    output_dir: Path = GOLD_DIR,
    candidate_limit: int = 250,
) -> EnhancedGoldOutputs:
    source = gold_file or latest_gold_file()
    run_stamp = stamp or timestamp()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.read_csv(source, low_memory=False)
    enhanced = _ensure_enhanced_columns(frame)
    decision_path = output_dir / f"gold_stock_decision_daily_{run_stamp}.csv"
    candidate_path = output_dir / f"gold_stock_candidates_latest_{run_stamp}.csv"
    catalog_path = output_dir / f"gold_stock_feature_catalog_{run_stamp}.csv"
    quality_path = output_dir / f"gold_stock_data_quality_report_{run_stamp}.csv"

    enhanced.to_csv(decision_path, index=False)
    latest_date = enhanced["date"].max()
    candidates = (
        enhanced[enhanced["date"].eq(latest_date)]
        .sort_values(["final_trade_score", "trade_confidence"], ascending=False)
        .head(candidate_limit)
    )
    candidates.to_csv(candidate_path, index=False)
    build_feature_catalog(list(enhanced.columns)).to_csv(catalog_path, index=False)
    build_quality_report(enhanced).to_csv(quality_path, index=False)
    return EnhancedGoldOutputs(decision_path, candidate_path, catalog_path, quality_path)
