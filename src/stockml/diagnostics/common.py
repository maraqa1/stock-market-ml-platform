from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import GOLD_DIR, MODEL_OUTPUTS_DIR, PORTAL_OUTPUTS_DIR, TRADING_DIR, latest_file


@dataclass(frozen=True)
class DiagnosticOutput:
    name: str
    path: Path
    rows: int
    status: str
    missing_inputs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def safe_read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def latest_model(pattern: str) -> Path | None:
    return latest_file(MODEL_OUTPUTS_DIR, pattern)


def latest_portal(pattern: str) -> Path | None:
    return latest_file(PORTAL_OUTPUTS_DIR, pattern)


def latest_trading(pattern: str) -> Path | None:
    candidates = sorted(TRADING_DIR.rglob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def missing_frame(report: str, missing_inputs: Iterable[str], note: str = "") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "report": report,
                "status": "missing_data",
                "missing_inputs": "|".join(missing_inputs),
                "note": note or "Required inputs were not available; no inference was made.",
            }
        ]
    )


def write_report(name: str, frame: pd.DataFrame, path: Path, missing_inputs: Iterable[str] = (), warnings: Iterable[str] = ()) -> DiagnosticOutput:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    missing = tuple(missing_inputs)
    warn = tuple(warnings)
    status = "missing_data" if missing else "ok"
    return DiagnosticOutput(name=name, path=path, rows=len(frame), status=status, missing_inputs=missing, warnings=warn)


def norm_symbol_column(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "ticker" not in out.columns and "symbol" in out.columns:
        out["ticker"] = out["symbol"]
    if "symbol" not in out.columns and "ticker" in out.columns:
        out["symbol"] = out["ticker"]
    if "ticker" in out.columns:
        out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    return out


def normal_side(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"long", "buy"}:
        return "Long"
    if text in {"short", "sell"}:
        return "Short"
    return "Unknown"


def add_side(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    source = "trade_action" if "trade_action" in out.columns else "side" if "side" in out.columns else ""
    out["diagnostic_side"] = out[source].map(normal_side) if source else "Unknown"
    return out


def numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def first_existing(frame: pd.DataFrame, columns: Iterable[str]) -> str | None:
    for column in columns:
        if column in frame.columns:
            return column
    return None


def latest_gold() -> Path | None:
    return latest_file(GOLD_DIR, "gold_stock_decision_daily_*.csv") or latest_file(GOLD_DIR, "06_us_gold_ml_dataset_*.csv")


OUTCOME_COLUMNS = [
    "ticker",
    "symbol",
    "date",
    "sector",
    "forward_5d_return",
    "forward_5d_alpha_vs_spy",
    "forward_5d_alpha_vs_sector",
    "target_return_5d",
    "target_sector_relative_return_5d",
]


def normalize_outcome_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = norm_symbol_column(frame)
    if "forward_5d_return" not in out.columns and "target_return_5d" in out.columns:
        out["forward_5d_return"] = out["target_return_5d"]
    if "forward_5d_alpha_vs_sector" not in out.columns and "target_sector_relative_return_5d" in out.columns:
        out["forward_5d_alpha_vs_sector"] = out["target_sector_relative_return_5d"]
    for column in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"]:
        if column not in out.columns:
            out[column] = pd.NA
    return out


def gold_outcome_slice(gold_path: Path | None, signals: pd.DataFrame, *, chunksize: int = 250_000) -> pd.DataFrame:
    if gold_path is None or not gold_path.exists() or gold_path.stat().st_size == 0 or signals.empty:
        return pd.DataFrame()
    keys = norm_symbol_column(signals)
    if not {"ticker", "date"}.issubset(keys.columns):
        return pd.DataFrame()
    keys["ticker"] = keys["ticker"].astype(str).str.upper().str.strip()
    keys["date"] = pd.to_datetime(keys["date"], errors="coerce").dt.date.astype(str)
    symbols = {symbol for symbol in keys["ticker"].dropna().astype(str) if symbol}
    dates = {date for date in keys["date"].dropna().astype(str) if date and date != "NaT"}
    if not symbols or not dates:
        return pd.DataFrame()
    chunks: list[pd.DataFrame] = []
    try:
        iterator = pd.read_csv(gold_path, usecols=lambda col: col in OUTCOME_COLUMNS, chunksize=chunksize, low_memory=False)
        for chunk in iterator:
            chunk = normalize_outcome_columns(chunk)
            if not {"ticker", "date"}.issubset(chunk.columns):
                continue
            chunk["ticker"] = chunk["ticker"].astype(str).str.upper().str.strip()
            chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce").dt.date.astype(str)
            selected = chunk[chunk["ticker"].isin(symbols) & chunk["date"].isin(dates)].copy()
            if not selected.empty:
                keep = [col for col in ["ticker", "date", "sector", "forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector"] if col in selected.columns]
                chunks.append(selected[keep])
    except (pd.errors.EmptyDataError, ValueError):
        return pd.DataFrame()
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True).drop_duplicates(["ticker", "date"], keep="last")


def attach_forward_returns(signals: pd.DataFrame, gold: pd.DataFrame) -> pd.DataFrame:
    out = norm_symbol_column(signals)
    if out.empty:
        return out
    for column in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"]:
        if column not in out.columns:
            out[column] = pd.NA
    if gold.empty or "ticker" not in out.columns or "date" not in out.columns:
        return out
    gold = normalize_outcome_columns(gold)
    keep = [col for col in ["ticker", "date", "forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"] if col in gold.columns]
    if {"ticker", "date"}.issubset(keep):
        merge = gold[keep].drop_duplicates(["ticker", "date"], keep="last")
        out = out.merge(merge, on=["ticker", "date"], how="left", suffixes=("", "_gold"))
        for column in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"]:
            gold_col = f"{column}_gold"
            if gold_col in out.columns:
                out[column] = out[column].fillna(out[gold_col])
                out = out.drop(columns=[gold_col])
    return out


def add_gain_columns(frame: pd.DataFrame, cost_bps: float = 10.0) -> pd.DataFrame:
    out = add_side(frame)
    side_sign = out["diagnostic_side"].map({"Long": 1.0, "Short": -1.0}).fillna(0.0)
    realized = numeric(out, "forward_5d_return", default=float("nan"))
    spy_alpha = numeric(out, "forward_5d_alpha_vs_spy", default=float("nan"))
    sector_alpha = numeric(out, "forward_5d_alpha_vs_sector", default=float("nan"))
    out["realized_5d_bps"] = realized * side_sign * 10000.0
    out["spy_alpha_5d_bps"] = spy_alpha * side_sign * 10000.0
    out["sector_alpha_5d_bps"] = sector_alpha * side_sign * 10000.0
    out["gain_after_cost_bps"] = out["realized_5d_bps"] - float(cost_bps)
    out["hit"] = out["gain_after_cost_bps"] > 0
    return out


def aggregate_edge(frame: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows = []
    grouped = frame.groupby(by, dropna=False)
    for keys, group in grouped:
        if not isinstance(keys, tuple):
            keys = (keys,)
        payload = dict(zip(by, keys))
        gains = pd.to_numeric(group.get("gain_after_cost_bps"), errors="coerce").dropna()
        payload.update(
            {
                "count": len(group),
                "observed_rows": int(gains.notna().sum()),
                "hit_rate": float(group.get("hit", pd.Series(False, index=group.index)).mean()) if len(group) else 0.0,
                "mean_realized_5d_bps": float(pd.to_numeric(group.get("realized_5d_bps"), errors="coerce").mean()),
                "mean_spy_alpha_5d_bps": float(pd.to_numeric(group.get("spy_alpha_5d_bps"), errors="coerce").mean()),
                "mean_sector_alpha_5d_bps": float(pd.to_numeric(group.get("sector_alpha_5d_bps"), errors="coerce").mean()),
                "mean_gain_after_cost_bps": float(gains.mean()) if not gains.empty else float("nan"),
                "median_gain_after_cost_bps": float(gains.median()) if not gains.empty else float("nan"),
                "min_gain_after_cost_bps": float(gains.min()) if not gains.empty else float("nan"),
            }
        )
        rows.append(payload)
    return pd.DataFrame(rows)


def add_rank_decile(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        return out
    if "date" not in out.columns:
        out["date"] = "unknown"
    score_col = first_existing(out, ["model_score", "rank_overall"])
    if score_col is None:
        out["score_bucket"] = "missing_score"
        return out
    score = pd.to_numeric(out[score_col], errors="coerce")
    out["_score_for_bucket"] = -score if score_col == "rank_overall" else score

    def bucket(group: pd.Series) -> pd.Series:
        valid = group.notna()
        result = pd.Series("missing_score", index=group.index, dtype="object")
        if valid.sum() == 0:
            return result
        ranked = group[valid].rank(method="first", pct=True)
        result.loc[valid] = (pd.cut(ranked, bins=[0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0], labels=[f"D{i}" for i in range(1, 11)], include_lowest=True).astype(str))
        return result

    out["score_bucket"] = out.groupby("date")["_score_for_bucket"].transform(bucket)
    return out.drop(columns=["_score_for_bucket"], errors="ignore")


def write_summary(outputs: list[DiagnosticOutput], path: Path) -> DiagnosticOutput:
    lines = ["# Strategy Diagnostics Summary", ""]
    for output in outputs:
        lines.append(f"## {output.name}")
        lines.append(f"- status: {output.status}")
        lines.append(f"- rows: {output.rows}")
        lines.append(f"- path: {output.path}")
        if output.missing_inputs:
            lines.append(f"- missing inputs: {', '.join(output.missing_inputs)}")
        if output.warnings:
            lines.append(f"- warnings: {'; '.join(output.warnings)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    missing = tuple(output.name for output in outputs if output.status == "missing_data")
    return DiagnosticOutput("summary", path, len(outputs), "missing_data" if missing else "ok", missing)
