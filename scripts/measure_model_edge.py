from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / "data" / "portal_outputs"
DEFAULT_GOLD_DIR = ROOT / "data" / "gold"
REPORT_DIR = ROOT / "reports" / "model_edge"


@dataclass
class MeasurementResult:
    label: str
    daily: pd.DataFrame
    rows: pd.DataFrame
    skipped_days: int
    missing_forward_rows: int
    missing_spy_rows: int


def _latest_gold_file(gold_dir: Path) -> Path | None:
    files = sorted(gold_dir.glob("06_us_gold_ml_dataset_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _artifact_timestamp(path: Path) -> datetime | None:
    match = re.search(r"(20\d{6})_(\d{6})", path.name)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _artifact_day(path: Path, frame: pd.DataFrame) -> pd.Timestamp | None:
    for column in ("date", "signal_date", "generated_at"):
        if column in frame.columns:
            parsed = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not parsed.empty:
                return pd.Timestamp(parsed.max()).normalize()
    stamp = _artifact_timestamp(path)
    return pd.Timestamp(stamp.date()) if stamp else None


def _score_column(frame: pd.DataFrame) -> str | None:
    for column in ("raw_score", "model_score", "risk_adjusted_score", "score", "confidence_score"):
        if column in frame.columns:
            return column
    return None


def _symbol_column(frame: pd.DataFrame) -> str | None:
    for column in ("symbol", "ticker"):
        if column in frame.columns:
            return column
    return None


def _direction(row: pd.Series) -> str:
    text = str(row.get("direction") or row.get("trade_action") or row.get("side") or "").strip().lower()
    if text in {"short", "sell"}:
        return "short"
    if text in {"long", "buy"}:
        return "long"
    return "long"


def _passed_gates(frame: pd.DataFrame) -> pd.Series:
    status = frame.get("trade_quality_status", pd.Series("", index=frame.index)).astype(str).str.lower()
    eligible = frame.get("order_eligible", pd.Series(True, index=frame.index))
    eligible = eligible.astype(str).str.lower().isin({"true", "1", "yes"}) | (eligible == True)  # noqa: E712
    return status.isin({"approved", "reduced"}) & eligible


def load_model_shortlists(artifact_dir: Path, max_days: int = 90) -> tuple[list[tuple[Path, pd.Timestamp, pd.DataFrame]], list[str]]:
    warnings: list[str] = []
    paths = sorted(artifact_dir.glob("08_alpaca_paper_candidate_pool_*.csv"), key=lambda path: path.stat().st_mtime)
    by_day: dict[pd.Timestamp, tuple[Path, pd.DataFrame]] = {}
    for path in paths:
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            warnings.append(f"skipped unreadable artifact {path}: {exc}")
            continue
        symbol_col = _symbol_column(frame)
        score_col = _score_column(frame)
        day = _artifact_day(path, frame)
        if frame.empty or symbol_col is None or score_col is None or day is None:
            warnings.append(f"skipped artifact with missing schema {path}")
            continue
        frame = frame.copy()
        frame["symbol"] = frame[symbol_col].astype(str).str.upper().str.strip()
        frame["raw_score"] = pd.to_numeric(frame[score_col], errors="coerce")
        frame["direction"] = frame.apply(_direction, axis=1)
        by_day[day] = (path, frame)
    recent_days = sorted(by_day)[-max_days:]
    return [(by_day[day][0], day, by_day[day][1]) for day in recent_days], warnings


def load_gold(path: Path) -> pd.DataFrame:
    gold = pd.read_csv(path, low_memory=False)
    if "ticker" not in gold.columns or "date" not in gold.columns:
        raise ValueError("Gold dataset must contain date and ticker columns.")
    price_col = "adj_close" if "adj_close" in gold.columns else "close"
    if price_col not in gold.columns:
        raise ValueError("Gold dataset must contain adj_close or close.")
    gold = gold.copy()
    gold["date"] = pd.to_datetime(gold["date"], errors="coerce").dt.normalize()
    gold["ticker"] = gold["ticker"].astype(str).str.upper().str.strip()
    gold[price_col] = pd.to_numeric(gold[price_col], errors="coerce")
    return gold.dropna(subset=["date", "ticker", price_col]).sort_values(["ticker", "date"]).rename(columns={price_col: "__price"})


def _next_trading_day(dates: list[pd.Timestamp], after_day: pd.Timestamp) -> pd.Timestamp | None:
    for day in dates:
        if day > after_day:
            return day
    return None


def _exit_day(dates: list[pd.Timestamp], entry_day: pd.Timestamp, horizon: int = 5) -> pd.Timestamp | None:
    try:
        idx = dates.index(entry_day)
    except ValueError:
        return None
    exit_idx = idx + horizon
    return dates[exit_idx] if exit_idx < len(dates) else None


def _entry_day_for_artifact(path: Path, artifact_day: pd.Timestamp, trading_days: list[pd.Timestamp]) -> tuple[pd.Timestamp | None, str]:
    stamp = _artifact_timestamp(path)
    if stamp and stamp.hour >= 20:
        return _next_trading_day(trading_days, artifact_day), "artifact timestamp after 20:00 UTC; entry uses next trading-day close"
    if artifact_day in trading_days:
        return artifact_day, "artifact timestamp not after 20:00 UTC or unavailable; entry uses artifact-day close"
    return _next_trading_day(trading_days, artifact_day), "artifact day missing in gold; entry uses next available trading-day close"


def _price_lookup(gold: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], float]:
    return {
        (str(row["ticker"]), pd.Timestamp(row["date"])): float(row["__price"])
        for _, row in gold.iterrows()
    }


def _select_top(frame: pd.DataFrame, passed_only: bool) -> pd.DataFrame:
    pool = frame[_passed_gates(frame)].copy() if passed_only else frame.copy()
    return pool.dropna(subset=["symbol", "raw_score"]).sort_values("raw_score", ascending=False).head(10)


def _record_return(row: pd.Series, prices: dict[tuple[str, pd.Timestamp], float], spy_return: float, entry_day: pd.Timestamp, exit_day: pd.Timestamp, cost: float) -> dict[str, Any] | None:
    symbol = str(row["symbol"]).upper()
    entry_price = prices.get((symbol, entry_day))
    exit_price = prices.get((symbol, exit_day))
    if not entry_price or not exit_price:
        return None
    raw_return = exit_price / entry_price - 1.0
    direction = _direction(row)
    directional_return = -raw_return if direction == "short" else raw_return
    directional_spy = -spy_return if direction == "short" else spy_return
    return {
        "symbol": symbol,
        "direction": direction,
        "sector": row.get("sector", "Unknown") or "Unknown",
        "liquidity_tier": row.get("liquidity_tier", "Unknown") or "Unknown",
        "volatility_tier": row.get("volatility_tier", "Unknown") or "Unknown",
        "risk_tier": row.get("risk_tier", "Unknown") or "Unknown",
        "raw_score": row.get("raw_score"),
        "entry_day": entry_day.date().isoformat(),
        "exit_day": exit_day.date().isoformat(),
        "symbol_return": directional_return,
        "spy_return": directional_spy,
        "excess_return_after_cost": directional_return - directional_spy - cost,
    }


def measure(shortlists: list[tuple[Path, pd.Timestamp, pd.DataFrame]], gold: pd.DataFrame, *, passed_only: bool, cost_bps: float) -> MeasurementResult:
    prices = _price_lookup(gold)
    trading_days = sorted(pd.Timestamp(day) for day in gold["date"].dropna().drop_duplicates())
    cost = cost_bps / 10_000.0
    rows: list[dict[str, Any]] = []
    skipped_days = 0
    missing_forward_rows = 0
    missing_spy_rows = 0

    for path, artifact_day, frame in shortlists:
        entry_day, convention = _entry_day_for_artifact(path, artifact_day, trading_days)
        if entry_day is None:
            skipped_days += 1
            continue
        exit_day = _exit_day(trading_days, entry_day, 5)
        if exit_day is None:
            skipped_days += 1
            continue
        spy_entry = prices.get(("SPY", entry_day))
        spy_exit = prices.get(("SPY", exit_day))
        if not spy_entry or not spy_exit:
            missing_spy_rows += 1
            skipped_days += 1
            continue
        spy_return = spy_exit / spy_entry - 1.0
        selected = _select_top(frame, passed_only)
        day_rows = []
        for _, row in selected.iterrows():
            measured = _record_return(row, prices, spy_return, entry_day, exit_day, cost)
            if measured is None:
                missing_forward_rows += 1
                continue
            measured["artifact"] = str(path)
            measured["model_day"] = artifact_day.date().isoformat()
            measured["entry_convention"] = convention
            day_rows.append(measured)
        if not day_rows:
            skipped_days += 1
            continue
        rows.extend(day_rows)

    row_frame = pd.DataFrame(rows)
    if row_frame.empty:
        daily = pd.DataFrame(columns=["model_day", "daily_excess_return"])
    else:
        daily = row_frame.groupby("model_day", as_index=False)["excess_return_after_cost"].mean().rename(
            columns={"excess_return_after_cost": "daily_excess_return"}
        )
    return MeasurementResult(
        label="passed-gates top 10" if passed_only else "raw-score top 10",
        daily=daily,
        rows=row_frame,
        skipped_days=skipped_days,
        missing_forward_rows=missing_forward_rows,
        missing_spy_rows=missing_spy_rows,
    )


def _metrics(daily: pd.DataFrame) -> dict[str, float | int | str]:
    values = pd.to_numeric(daily.get("daily_excess_return", pd.Series(dtype=float)), errors="coerce").dropna()
    n = int(len(values))
    mean = float(values.mean()) if n else 0.0
    std = float(values.std(ddof=1)) if n > 1 else 0.0
    t_stat = mean / (std / math.sqrt(n)) if n > 1 and std else 0.0
    sharpe = mean / std * math.sqrt(252 / 5) if std else 0.0
    if mean > 0.003 and t_stat > 2:
        status = "GREEN"
    elif mean > 0.003:
        status = "AMBER_WITH_WEAK_SIGNIFICANCE"
    elif mean >= 0:
        status = "AMBER"
    else:
        status = "RED"
    return {
        "valid_days": n,
        "mean": mean,
        "median": float(values.median()) if n else 0.0,
        "std": std,
        "hit_rate": float((values > 0).mean()) if n else 0.0,
        "sharpe": sharpe,
        "t_stat": t_stat,
        "status": status,
    }


def _slice_table(rows: pd.DataFrame, column: str) -> str:
    if rows.empty or column not in rows.columns:
        return "_No valid rows._\n"
    grouped = rows.groupby(column)["excess_return_after_cost"].agg(["count", "mean", "median"]).reset_index()
    lines = [f"| {column} | count | mean bps | median bps |", "| --- | ---: | ---: | ---: |"]
    for row in grouped.itertuples(index=False):
        lines.append(f"| {getattr(row, column)} | {int(row.count)} | {row.mean * 10000:.1f} | {row.median * 10000:.1f} |")
    return "\n".join(lines) + "\n"


def _section(result: MeasurementResult) -> str:
    metrics = _metrics(result.daily)
    lines = [
        f"## {result.label}",
        "",
        f"- Valid days: {metrics['valid_days']}",
        f"- Skipped days: {result.skipped_days}",
        f"- Missing forward-return rows: {result.missing_forward_rows}",
        f"- Missing SPY rows: {result.missing_spy_rows}",
        f"- Mean daily excess return: {metrics['mean'] * 10000:.1f} bps",
        f"- Median daily excess return: {metrics['median'] * 10000:.1f} bps",
        f"- Std: {metrics['std'] * 10000:.1f} bps",
        f"- Hit rate: {metrics['hit_rate']:.1%}",
        f"- Sharpe: {metrics['sharpe']:.2f}",
        f"- t-stat: {metrics['t_stat']:.2f}",
        f"- Status: {metrics['status']}",
        "",
    ]
    for column in ["direction", "sector", "liquidity_tier", "volatility_tier", "risk_tier"]:
        lines.extend([f"### Slice by {column}", "", _slice_table(result.rows, column), ""])
    return "\n".join(lines)


def write_report(raw: MeasurementResult, passed: MeasurementResult, *, gold_path: Path | None, artifacts: list[tuple[Path, pd.Timestamp, pd.DataFrame]], warnings: list[str], cost_bps: float, cost_source: str, output_date: date | None = None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_date = output_date or datetime.now(timezone.utc).date()
    path = REPORT_DIR / f"{report_date.isoformat()}.md"
    raw_metrics = _metrics(raw.daily)
    lines = [
        "# Model Edge Measurement",
        "",
        f"- Daily mean excess return: {raw_metrics['mean'] * 10000:.1f} bps",
        f"- t-stat: {raw_metrics['t_stat']:.2f}",
        f"- Status: {raw_metrics['status']}",
        "",
        "## Scope",
        "",
        f"- Model shortlist artifacts loaded: {len(artifacts)}",
        f"- Gold dataset: {gold_path if gold_path else 'missing'}",
        f"- Costs applied: yes, {cost_bps:.1f} bps round trip ({cost_source})",
        "- Entry/exit convention: daily gold close prices. After-close artifacts enter next trading-day close; otherwise artifact-day close is used. Exit is five trading days after entry.",
        "- Artifact schema assumptions: symbol/ticker column, score from raw_score/model_score/risk_adjusted_score/score/confidence_score, gate pass from trade_quality_status in approved/reduced and order_eligible true.",
        "- t-stat is calculated across daily top-10 portfolio observations, not individual symbols.",
        "",
    ]
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings[:50])
        lines.append("")
    lines.append(_section(raw))
    lines.append(_section(passed))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _cost_bps() -> tuple[float, str]:
    try:
        from stockml.trading.config import alpaca_config

        return float(alpaca_config().transaction_cost_bps), "stockml.trading.config.alpaca_config"
    except Exception:
        return 10.0, "default because no cost config could be loaded"


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure realized edge of recent model shortlist artifacts.")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--gold", type=Path, default=None)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    cost_bps, cost_source = _cost_bps()
    artifacts, warnings = load_model_shortlists(args.artifact_dir, max_days=args.days)
    gold_path = args.gold or _latest_gold_file(DEFAULT_GOLD_DIR)
    if gold_path is None:
        raw = MeasurementResult("raw-score top 10", pd.DataFrame(), pd.DataFrame(), len(artifacts), 0, 0)
        passed = MeasurementResult("passed-gates top 10", pd.DataFrame(), pd.DataFrame(), len(artifacts), 0, 0)
        warnings.append("No gold dataset found; edge metrics could not be measured.")
        report = write_report(raw, passed, gold_path=None, artifacts=artifacts, warnings=warnings, cost_bps=cost_bps, cost_source=cost_source)
        print(f"model_edge_report: {report}")
        return 0

    gold = load_gold(gold_path)
    raw = measure(artifacts, gold, passed_only=False, cost_bps=cost_bps)
    passed = measure(artifacts, gold, passed_only=True, cost_bps=cost_bps)
    report = write_report(raw, passed, gold_path=gold_path, artifacts=artifacts, warnings=warnings, cost_bps=cost_bps, cost_source=cost_source)
    print(f"model_edge_report: {report}")
    print(f"raw_status: {_metrics(raw.daily)['status']}")
    print(f"passed_gates_status: {_metrics(passed.daily)['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
