from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import GOLD_DIR, HOLDING_PERIOD_DIR, PORTAL_OUTPUTS_DIR, ensure_data_dirs, latest_file, timestamp


HORIZONS = (1, 3, 5, 10)
OUTPUT_COLUMNS = [
    "symbol",
    "side",
    "trade_action",
    "trading_stream",
    "status",
    "notional",
    "suggested_quantity",
    "current_price",
    "risk_tier",
    "volatility_tier",
    "liquidity_tier",
    "recommended_holding_days",
    "review_after_days",
    "max_holding_days",
    "best_horizon_score",
    "expected_directional_return_bps",
    "median_directional_return_bps",
    "hit_rate",
    "sample_count",
    "stop_loss_price",
    "take_profit_price",
    "exit_rule",
    "holding_period_reason",
]

REVIEW_COLUMNS = [
    *OUTPUT_COLUMNS,
    "holding_quality",
    "recommended_action",
    "holding_gate_pass",
    "holding_gate_reason",
]


@dataclass(frozen=True)
class HorizonStats:
    horizon_days: int
    sample_count: int
    mean_bps: float
    median_bps: float
    hit_rate: float
    p25_bps: float
    p75_bps: float

    @property
    def score(self) -> float:
        sample_penalty = 0.0 if self.sample_count >= 100 else -25.0
        return (self.median_bps / math.sqrt(self.horizon_days)) + ((self.hit_rate - 0.5) * 100.0) + sample_penalty


def latest_plan_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else PORTAL_OUTPUTS_DIR.parent.parent
    return latest_file(base / "data" / "portal_outputs", "08_alpaca_paper_order_plan_*.csv")


def latest_positions_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else PORTAL_OUTPUTS_DIR.parent.parent
    return latest_file(base / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv")


def latest_gold_path(root: Path | None = None) -> Path | None:
    base = Path(root).resolve() if root else GOLD_DIR.parent.parent
    return latest_file(base / "data" / "gold", "06_us_gold_ml_dataset_*.csv")


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _symbol_set(plan: pd.DataFrame) -> set[str]:
    if plan.empty or "symbol" not in plan.columns:
        return set()
    return {str(value).upper().strip() for value in plan["symbol"].dropna() if str(value).strip()}


def _open_positions_as_plan_rows(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty or "symbol" not in positions.columns:
        return pd.DataFrame()
    rows = []
    for row in positions.fillna("").to_dict("records"):
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        qty = pd.to_numeric(row.get("qty", row.get("quantity", "")), errors="coerce")
        side_text = str(row.get("side") or "").strip().lower()
        is_short = side_text == "short" or (pd.notna(qty) and float(qty) < 0)
        price = row.get("current_price") or row.get("last_price") or row.get("avg_entry_price") or row.get("market_price") or ""
        rows.append(
            {
                "symbol": symbol,
                "side": "sell" if is_short else "buy",
                "trade_action": "Short" if is_short else "Long",
                "trade_quality_status": "open_position",
                "notional": abs(float(row.get("market_value") or 0)) if str(row.get("market_value") or "").strip() else "",
                "suggested_quantity": abs(float(qty)) if pd.notna(qty) else "",
                "current_price": price,
                "risk_tier": row.get("risk_tier", ""),
                "volatility_tier": row.get("volatility_tier", ""),
                "liquidity_tier": row.get("liquidity_tier", ""),
                "max_holding_days": row.get("max_holding_days", 10),
                "stop_loss_price": row.get("stop_loss_price", ""),
                "take_profit_price": row.get("take_profit_price", ""),
            }
        )
    return pd.DataFrame(rows)


def _append_open_positions_to_plan(plan: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    position_rows = _open_positions_as_plan_rows(positions)
    if position_rows.empty:
        return plan
    if plan.empty:
        return position_rows
    out = pd.concat([plan, position_rows], ignore_index=True, sort=False)
    out["__symbol"] = out["symbol"].fillna("").astype(str).str.upper().str.strip()
    out["__source_priority"] = 0
    out.loc[out.index >= len(plan), "__source_priority"] = 1
    out = out.sort_values(["__symbol", "__source_priority"]).drop_duplicates("__symbol", keep="last")
    return out.drop(columns=["__symbol", "__source_priority"])


def load_gold_history_for_symbols(gold_path: Path, symbols: Iterable[str], chunksize: int = 250_000) -> pd.DataFrame:
    wanted = {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()}
    if not wanted:
        return pd.DataFrame()
    required = {"date", "ticker", "adj_close", "close"}
    header = pd.read_csv(gold_path, nrows=0)
    usecols = [column for column in header.columns if column in required]
    frames: list[pd.DataFrame] = []
    for chunk in pd.read_csv(gold_path, usecols=usecols, chunksize=chunksize, low_memory=False):
        chunk["ticker"] = chunk["ticker"].astype(str).str.upper().str.strip()
        subset = chunk[chunk["ticker"].isin(wanted)].copy()
        if not subset.empty:
            frames.append(subset)
    if not frames:
        return pd.DataFrame(columns=usecols)
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    return out.dropna(subset=["date", "ticker"]).sort_values(["ticker", "date"]).reset_index(drop=True)


def _price_series(frame: pd.DataFrame) -> pd.Series:
    price_col = "adj_close" if "adj_close" in frame.columns else "close"
    return pd.to_numeric(frame[price_col], errors="coerce")


def horizon_stats(history: pd.DataFrame, symbol: str, side: str, horizons: Iterable[int] = HORIZONS) -> list[HorizonStats]:
    frame = history[history["ticker"].astype(str).str.upper().eq(str(symbol).upper())].copy()
    if frame.empty:
        return []
    frame = frame.sort_values("date")
    price = _price_series(frame)
    is_short = str(side or "").strip().lower() in {"sell", "short"}
    rows: list[HorizonStats] = []
    for horizon in horizons:
        returns = price.shift(-horizon) / price - 1.0
        if is_short:
            returns = -returns
        values = pd.to_numeric(returns, errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
        if values.empty:
            continue
        bps = values * 10_000.0
        rows.append(
            HorizonStats(
                horizon_days=int(horizon),
                sample_count=int(len(bps)),
                mean_bps=float(bps.mean()),
                median_bps=float(bps.median()),
                hit_rate=float((bps > 0).mean()),
                p25_bps=float(bps.quantile(0.25)),
                p75_bps=float(bps.quantile(0.75)),
            )
        )
    return rows


def choose_holding_period(stats: list[HorizonStats], row: pd.Series) -> tuple[int, int, int, HorizonStats | None, str]:
    if not stats:
        configured = pd.to_numeric(row.get("max_holding_days"), errors="coerce")
        fallback_max = int(configured) if pd.notna(configured) and configured > 0 else 3
        return 1, 1, fallback_max, None, "insufficient_symbol_history"
    viable = [item for item in stats if item.median_bps > 0 and item.hit_rate >= 0.52]
    selected = max(viable or stats, key=lambda item: item.score)
    risk_tier = str(row.get("risk_tier") or "").strip().lower()
    volatility = str(row.get("volatility_tier") or "").strip().lower()
    configured_max = pd.to_numeric(row.get("max_holding_days"), errors="coerce")
    configured_max_days = int(configured_max) if pd.notna(configured_max) and configured_max > 0 else 10
    if selected.horizon_days <= 1:
        max_days = 1
        review_after = 1
        reason = "same_day_edge_window"
    elif risk_tier == "speculative" or volatility in {"high", "extreme"}:
        max_days = min(configured_max_days, max(selected.horizon_days, 3))
        review_after = 1
        reason = "shorter_hold_due_to_risk_or_volatility"
    elif selected.horizon_days <= 3:
        max_days = min(configured_max_days, 5)
        review_after = 1
        reason = "short_edge_window"
    else:
        max_days = min(configured_max_days, max(selected.horizon_days, 5))
        review_after = min(2, selected.horizon_days)
        reason = "historical_edge_window"
    return selected.horizon_days, review_after, max_days, selected, reason


def build_holding_period_report(plan: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if plan.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    rows = []
    for _, row in plan.iterrows():
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        stats = horizon_stats(history, symbol, str(row.get("side") or row.get("trade_action") or ""))
        holding_days, review_after, max_days, selected, reason = choose_holding_period(stats, row)
        trading_stream = "same_day" if max_days <= 1 else "multi_day"
        exit_rule = "stop_or_take_profit_first;daily_review;close_at_max_holding_days"
        rows.append(
            {
                "symbol": symbol,
                "side": row.get("side", ""),
                "trade_action": row.get("trade_action", ""),
                "trading_stream": trading_stream,
                "status": row.get("trade_quality_status", row.get("status", "")),
                "notional": row.get("notional", row.get("approved_notional", "")),
                "suggested_quantity": row.get("suggested_quantity", ""),
                "current_price": row.get("current_price", ""),
                "risk_tier": row.get("risk_tier", ""),
                "volatility_tier": row.get("volatility_tier", ""),
                "liquidity_tier": row.get("liquidity_tier", ""),
                "recommended_holding_days": holding_days,
                "review_after_days": review_after,
                "max_holding_days": max_days,
                "best_horizon_score": selected.score if selected else pd.NA,
                "expected_directional_return_bps": selected.mean_bps if selected else pd.NA,
                "median_directional_return_bps": selected.median_bps if selected else pd.NA,
                "hit_rate": selected.hit_rate if selected else pd.NA,
                "sample_count": selected.sample_count if selected else 0,
                "stop_loss_price": row.get("stop_loss_price", ""),
                "take_profit_price": row.get("take_profit_price", ""),
                "exit_rule": exit_rule,
                "holding_period_reason": reason,
            }
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def classify_holding_quality(row: pd.Series) -> tuple[str, str, bool, str]:
    median = pd.to_numeric(row.get("median_directional_return_bps"), errors="coerce")
    hit_rate = pd.to_numeric(row.get("hit_rate"), errors="coerce")
    sample_count = pd.to_numeric(row.get("sample_count"), errors="coerce")
    if pd.isna(sample_count) or sample_count < 250:
        return "watch", "manual_review", False, "insufficient_holding_sample"
    if pd.isna(median) or pd.isna(hit_rate):
        return "watch", "manual_review", False, "holding_stats_missing"
    if median > 50 and hit_rate >= 0.55:
        return "strong", "trade_normal_size", True, "positive_holding_edge_strong"
    if median > 0 and hit_rate >= 0.52:
        return "watch", "trade_reduced_size_or_wait_for_intraday_confirmation", True, "positive_holding_edge_watch"
    return "avoid", "skip_or_require_manual_override", False, "holding_edge_not_confirmed"


def build_holding_review(plan: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    report = build_holding_period_report(plan, history)
    if report.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    rows = []
    for _, row in report.iterrows():
        quality, action, passed, reason = classify_holding_quality(row)
        rows.append(
            {
                **row.to_dict(),
                "holding_quality": quality,
                "recommended_action": action,
                "holding_gate_pass": passed,
                "holding_gate_reason": reason,
            }
        )
    return pd.DataFrame(rows, columns=REVIEW_COLUMNS)


def write_holding_period_report(frame: pd.DataFrame, output_dir: Path | None = None, stamp: str | None = None) -> Path:
    ensure_data_dirs()
    directory = output_dir or HOLDING_PERIOD_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"holding_period_report_{stamp or timestamp()}.csv"
    frame.to_csv(path, index=False)
    return path


def write_holding_review(frame: pd.DataFrame, output_dir: Path | None = None, stamp: str | None = None) -> Path:
    ensure_data_dirs()
    directory = output_dir or HOLDING_PERIOD_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"holding_review_{stamp or timestamp()}.csv"
    frame.to_csv(path, index=False)
    return path


def generate_holding_period_report(
    root: Path | None = None,
    plan_file: Path | None = None,
    gold_file: Path | None = None,
    position_file: Path | None = None,
    stamp: str | None = None,
    include_open_positions: bool = True,
) -> dict[str, object]:
    base = Path(root).resolve() if root else None
    plan_path = plan_file or latest_plan_path(base)
    gold_path = gold_file or latest_gold_path(base)
    positions_path = position_file or (latest_positions_path(base) if include_open_positions else None)
    if plan_path is None or gold_path is None:
        frame = pd.DataFrame(columns=OUTPUT_COLUMNS)
        review = pd.DataFrame(columns=REVIEW_COLUMNS)
    else:
        plan = _read_csv(plan_path)
        if include_open_positions:
            plan = _append_open_positions_to_plan(plan, _read_csv(positions_path))
        history = load_gold_history_for_symbols(gold_path, _symbol_set(plan))
        frame = build_holding_period_report(plan, history)
        review = build_holding_review(plan, history)
    output_dir = (base / "data" / "trading" / "holding_period") if base else HOLDING_PERIOD_DIR
    output_path = write_holding_period_report(frame, output_dir=output_dir, stamp=stamp)
    review_path = write_holding_review(review, output_dir=output_dir, stamp=stamp)
    return {
        "status": "ok",
        "rows": int(len(frame)),
        "path": str(output_path),
        "review_rows": int(len(review)),
        "review_path": str(review_path),
        "review_passed": int(review["holding_gate_pass"].sum()) if "holding_gate_pass" in review.columns else 0,
        "review_blocked": int((~review["holding_gate_pass"].astype(bool)).sum()) if "holding_gate_pass" in review.columns else 0,
        "plan_path": str(plan_path or ""),
        "gold_path": str(gold_path or ""),
        "positions_path": str(positions_path or ""),
    }
