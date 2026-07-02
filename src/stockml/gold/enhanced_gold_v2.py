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
    "ticker_direction_memory_scope",
    "ticker_direction_memory_status",
    "ticker_direction_sample_count",
    "ticker_long_win_rate_5d",
    "ticker_short_win_rate_5d",
    "ticker_avg_long_alpha_bps_5d",
    "ticker_avg_short_alpha_bps_5d",
    "ticker_direction_bias_gold",
    "ticker_direction_reason_gold",
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
    "direction_memory": {
        "ticker_direction_memory_scope",
        "ticker_direction_memory_status",
        "ticker_direction_sample_count",
        "ticker_long_win_rate_5d",
        "ticker_short_win_rate_5d",
        "ticker_avg_long_alpha_bps_5d",
        "ticker_avg_short_alpha_bps_5d",
        "ticker_direction_bias_gold",
        "ticker_direction_reason_gold",
    },
}


@dataclass(frozen=True)
class EnhancedGoldOutputs:
    decision_daily: Path
    candidates_latest: Path
    feature_catalog: Path
    data_quality_report: Path


@dataclass
class _QualityStats:
    rows: int = 0
    duplicate_ticker_date_count: int = 0
    last_key: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        self.tickers: set[str] = set()
        self.dates: set[str] = set()
        self.label_counts: dict[str, int] = {}
        self.family_missing: dict[str, int] = {family: 0 for family in FEATURE_FAMILIES}
        self.family_cells: dict[str, int] = {family: 0 for family in FEATURE_FAMILIES}
        self.score_min: dict[str, float] = {}
        self.score_max: dict[str, float] = {}

    def update(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        self.rows += len(frame)
        if "ticker" in frame.columns:
            self.tickers.update(frame["ticker"].dropna().astype(str).str.upper())
        if "date" in frame.columns:
            self.dates.update(frame["date"].dropna().astype(str))
        if {"ticker", "date"}.issubset(frame.columns):
            keys = frame[["ticker", "date"]].astype(str).itertuples(index=False, name=None)
            previous = self.last_key
            for key in keys:
                if key == previous:
                    self.duplicate_ticker_date_count += 1
                previous = key
            self.last_key = previous
        if "target_trade_label_5d" in frame.columns:
            counts = frame["target_trade_label_5d"].astype("string").value_counts(dropna=False)
            for label, count in counts.items():
                key = str(label)
                self.label_counts[key] = self.label_counts.get(key, 0) + int(count)
        for family, columns in FEATURE_FAMILIES.items():
            present = [column for column in columns if column in frame.columns]
            if not present:
                continue
            family_frame = frame[present]
            self.family_missing[family] += int(family_frame.isna().sum().sum())
            self.family_cells[family] += int(family_frame.size)
        for column in ["momentum_score", "relative_strength_score", "technical_entry_score", "final_trade_score", "trade_confidence"]:
            if column not in frame.columns:
                continue
            values = pd.to_numeric(frame[column], errors="coerce")
            min_value = values.min(skipna=True)
            max_value = values.max(skipna=True)
            if pd.notna(min_value):
                self.score_min[column] = min(float(min_value), self.score_min.get(column, float(min_value)))
            if pd.notna(max_value):
                self.score_max[column] = max(float(max_value), self.score_max.get(column, float(max_value)))


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


def _ensure_enhanced_columns(frame: pd.DataFrame, *, direction_state: dict[str, dict[str, float]] | None = None) -> pd.DataFrame:
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
    out = _add_gold_direction_memory(out, state=direction_state)
    for column in DECISION_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    return out


def _add_gold_direction_memory(
    frame: pd.DataFrame,
    *,
    min_samples: int = 20,
    state: dict[str, dict[str, float]] | None = None,
) -> pd.DataFrame:
    """Add ticker-level direction evidence from prior Gold labels only.

    The current row's forward label is shifted out of the expanding window, so
    these fields are historical evidence, not current-row leakage.
    """
    out = frame.sort_values(["date", "ticker"], kind="mergesort").copy()
    alpha = pd.to_numeric(out.get("forward_5d_alpha_vs_sector"), errors="coerce")
    alpha_bps = alpha * 10_000.0
    if state is not None:
        out["ticker_direction_sample_count"] = 0
        out["ticker_avg_long_alpha_bps_5d"] = pd.NA
        out["ticker_avg_short_alpha_bps_5d"] = pd.NA
        out["ticker_long_win_rate_5d"] = pd.NA
        out["ticker_short_win_rate_5d"] = pd.NA
        for idx, row in out.iterrows():
            ticker = str(row.get("ticker") or "").upper()
            stats = state.setdefault(ticker, {"count": 0.0, "sum_bps": 0.0, "long_wins": 0.0, "short_wins": 0.0})
            count = int(stats["count"])
            out.at[idx, "ticker_direction_sample_count"] = count
            if count > 0:
                avg_long = stats["sum_bps"] / count
                out.at[idx, "ticker_avg_long_alpha_bps_5d"] = avg_long
                out.at[idx, "ticker_avg_short_alpha_bps_5d"] = -avg_long
                out.at[idx, "ticker_long_win_rate_5d"] = stats["long_wins"] / count
                out.at[idx, "ticker_short_win_rate_5d"] = stats["short_wins"] / count
            value = alpha_bps.loc[idx] if idx in alpha_bps.index else pd.NA
            if pd.notna(value):
                stats["count"] += 1.0
                stats["sum_bps"] += float(value)
                stats["long_wins"] += 1.0 if float(value) > 0 else 0.0
                stats["short_wins"] += 1.0 if float(value) < 0 else 0.0
        return _finalize_gold_direction_memory(out, min_samples=min_samples).sort_index()

    valid = alpha_bps.notna()
    long_win = alpha_bps.gt(0).where(valid)
    short_win = alpha_bps.lt(0).where(valid)

    group = out["ticker"]
    out["ticker_direction_sample_count"] = (
        valid.astype(int).groupby(group).cumsum().groupby(group).shift(1).fillna(0).astype(int)
    )
    out["ticker_avg_long_alpha_bps_5d"] = alpha_bps.groupby(group).transform(lambda series: series.expanding().mean().shift(1))
    out["ticker_avg_short_alpha_bps_5d"] = -out["ticker_avg_long_alpha_bps_5d"]
    out["ticker_long_win_rate_5d"] = long_win.astype("float").groupby(group).transform(lambda series: series.expanding().mean().shift(1))
    out["ticker_short_win_rate_5d"] = short_win.astype("float").groupby(group).transform(lambda series: series.expanding().mean().shift(1))
    out = _finalize_gold_direction_memory(out, min_samples=min_samples)
    return out.sort_index()


def _finalize_gold_direction_memory(frame: pd.DataFrame, *, min_samples: int) -> pd.DataFrame:
    out = frame.copy()
    out["ticker_direction_memory_scope"] = "ticker"
    out["ticker_direction_memory_status"] = "insufficient_samples"
    enough = out["ticker_direction_sample_count"].ge(min_samples)
    out.loc[enough, "ticker_direction_memory_status"] = "available"
    long_edge = pd.to_numeric(out["ticker_avg_long_alpha_bps_5d"], errors="coerce")
    short_edge = pd.to_numeric(out["ticker_avg_short_alpha_bps_5d"], errors="coerce")
    out["ticker_direction_bias_gold"] = "insufficient_data"
    out.loc[enough & long_edge.gt(25) & long_edge.gt(short_edge), "ticker_direction_bias_gold"] = "trust_long"
    out.loc[enough & short_edge.gt(25) & short_edge.gt(long_edge), "ticker_direction_bias_gold"] = "trust_short"
    out.loc[enough & long_edge.le(0) & short_edge.le(0), "ticker_direction_bias_gold"] = "no_trade"
    out["ticker_direction_reason_gold"] = "insufficient_historical_gold_samples"
    out.loc[out["ticker_direction_bias_gold"].eq("trust_long"), "ticker_direction_reason_gold"] = "historical_ticker_long_alpha_positive"
    out.loc[out["ticker_direction_bias_gold"].eq("trust_short"), "ticker_direction_reason_gold"] = "historical_ticker_short_alpha_positive"
    out.loc[out["ticker_direction_bias_gold"].eq("no_trade"), "ticker_direction_reason_gold"] = "historical_ticker_alpha_not_positive"
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
    stats = _QualityStats()
    stats.update(frame)
    return _quality_report_from_stats(stats, list(frame.columns), min_rows=min_rows, min_dates=min_dates)


def _quality_report_from_stats(stats: _QualityStats, columns: list[str], *, min_rows: int = 10, min_dates: int = 5) -> pd.DataFrame:
    label_counts = stats.label_counts
    non_na_labels = {str(k): int(v) for k, v in label_counts.items() if str(k) not in {"<NA>", "nan", "None"}}
    all_neutral = bool(non_na_labels) and set(non_na_labels) == {"Neutral"}
    rows = [
        {"check": "row_count", "observed": stats.rows, "status": "pass" if stats.rows >= min_rows else "fail", "message": f">={min_rows}"},
        {"check": "ticker_count", "observed": len(stats.tickers), "status": "info", "message": ""},
        {"check": "date_count", "observed": len(stats.dates), "status": "pass" if len(stats.dates) >= min_dates else "fail", "message": f">={min_dates}"},
        {"check": "duplicate_ticker_date_count", "observed": stats.duplicate_ticker_date_count, "status": "pass" if stats.duplicate_ticker_date_count == 0 else "fail", "message": ""},
        {"check": "target_label_distribution", "observed": label_counts, "status": "fail" if all_neutral else "pass", "message": "non-NA labels must not be all Neutral"},
        {"check": "leakage_check", "observed": leakage_columns(columns), "status": "pass", "message": "target/forward columns excluded by catalog"},
        {"check": "score_range_check", "observed": _score_range_from_stats(stats), "status": "pass" if _score_range_stats_ok(stats) else "fail", "message": "scores must be in [0,1]"},
    ]
    available_columns = set(columns)
    for family, family_columns in FEATURE_FAMILIES.items():
        cells = stats.family_cells.get(family, 0)
        missing_rate = float(stats.family_missing.get(family, 0) / cells) if cells else 1.0
        present = [column for column in family_columns if column in available_columns]
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


def _score_range_from_stats(stats: _QualityStats) -> dict[str, Any]:
    return {
        column: {"min": stats.score_min.get(column), "max": stats.score_max.get(column)}
        for column in sorted(set(stats.score_min) | set(stats.score_max))
    }


def _score_range_stats_ok(stats: _QualityStats) -> bool:
    for column in set(stats.score_min) | set(stats.score_max):
        if stats.score_min.get(column) is not None and stats.score_min[column] < 0:
            return False
        if stats.score_max.get(column) is not None and stats.score_max[column] > 1:
            return False
    return True


def _iter_complete_date_chunks(source: Path, *, chunk_size: int) -> Any:
    carry = pd.DataFrame()
    for chunk in pd.read_csv(source, chunksize=chunk_size, low_memory=False):
        combined = pd.concat([carry, chunk], ignore_index=True) if not carry.empty else chunk
        if combined.empty:
            carry = combined
            continue
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        last_date = combined["date"].iloc[-1]
        if pd.isna(last_date):
            yield combined
            carry = pd.DataFrame()
            continue
        complete = combined[~combined["date"].eq(last_date)]
        carry = combined[combined["date"].eq(last_date)].copy()
        if not complete.empty:
            yield complete
    if not carry.empty:
        yield carry


def build_enhanced_gold_v2(
    gold_file: Path | None = None,
    *,
    stamp: str | None = None,
    output_dir: Path = GOLD_DIR,
    candidate_limit: int = 250,
    chunk_size: int = 200_000,
) -> EnhancedGoldOutputs:
    source = gold_file or latest_gold_file()
    run_stamp = stamp or timestamp()
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_path = output_dir / f"gold_stock_decision_daily_{run_stamp}.csv"
    candidate_path = output_dir / f"gold_stock_candidates_latest_{run_stamp}.csv"
    catalog_path = output_dir / f"gold_stock_feature_catalog_{run_stamp}.csv"
    quality_path = output_dir / f"gold_stock_data_quality_report_{run_stamp}.csv"

    if decision_path.exists():
        decision_path.unlink()
    source_columns = pd.read_csv(source, nrows=0).columns.tolist()
    enhanced_columns = list(dict.fromkeys(source_columns + DECISION_COLUMNS))
    stats = _QualityStats()
    latest_date = pd.NaT
    latest_candidates = pd.DataFrame()
    wrote_header = False
    direction_state: dict[str, dict[str, float]] = {}

    for chunk in _iter_complete_date_chunks(source, chunk_size=chunk_size):
        enhanced = _ensure_enhanced_columns(chunk, direction_state=direction_state)
        enhanced.to_csv(decision_path, mode="a", header=not wrote_header, index=False)
        wrote_header = True
        stats.update(enhanced)

        chunk_latest = enhanced["date"].max()
        if pd.isna(chunk_latest):
            continue
        chunk_latest_rows = enhanced[enhanced["date"].eq(chunk_latest)]
        if pd.isna(latest_date) or chunk_latest > latest_date:
            latest_date = chunk_latest
            latest_candidates = chunk_latest_rows.copy()
        elif chunk_latest == latest_date:
            latest_candidates = pd.concat([latest_candidates, chunk_latest_rows], ignore_index=True)

    if not wrote_header:
        pd.DataFrame(columns=enhanced_columns).to_csv(decision_path, index=False)

    if latest_candidates.empty:
        candidates = pd.DataFrame(columns=enhanced_columns)
    else:
        candidates = latest_candidates.sort_values(["final_trade_score", "trade_confidence"], ascending=False).head(candidate_limit)
    candidates.to_csv(candidate_path, index=False)
    build_feature_catalog(enhanced_columns).to_csv(catalog_path, index=False)
    _quality_report_from_stats(stats, enhanced_columns).to_csv(quality_path, index=False)
    return EnhancedGoldOutputs(decision_path, candidate_path, catalog_path, quality_path)
