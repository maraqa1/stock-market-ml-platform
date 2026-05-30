from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class MissedOpportunityReport:
    session_date: date
    rows: list[dict[str, Any]]
    markdown: str


def _symbol_set(frame: pd.DataFrame) -> set[str]:
    if frame.empty or "symbol" not in frame.columns:
        return set()
    return {str(symbol).upper().strip() for symbol in frame["symbol"].dropna() if str(symbol).strip()}


def _session_rows(frame: pd.DataFrame, session_date: date, *, timestamp_col: str = "timestamp") -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if timestamp_col not in frame.columns:
        return frame.copy()
    out = frame.copy()
    out[timestamp_col] = pd.to_datetime(out[timestamp_col], errors="coerce", utc=True)
    return out[out[timestamp_col].dt.date == session_date].copy()


def _first_blocking_gate(logs: pd.DataFrame) -> str | None:
    if logs.empty:
        return None
    blocked = logs[logs.get("block_reason", pd.Series("", index=logs.index)).fillna("").astype(str).ne("")]
    if blocked.empty:
        return None
    if "decision_time" in blocked.columns:
        blocked = blocked.sort_values("decision_time")
    return str(blocked.iloc[0].get("block_reason") or "")


def _max_continuation(logs: pd.DataFrame) -> float | None:
    if logs.empty or "continuation_probability" not in logs.columns:
        return None
    values = pd.to_numeric(logs["continuation_probability"], errors="coerce").dropna()
    return float(values.max()) if not values.empty else None


def _hypothetical_pnl_bps(day_bars: pd.DataFrame) -> float | None:
    if day_bars.empty or not {"open", "high", "low", "close"}.issubset(day_bars.columns):
        return None
    bars = day_bars.sort_values("timestamp") if "timestamp" in day_bars.columns else day_bars
    entry = pd.to_numeric(bars.iloc[0].get("open"), errors="coerce")
    exit_price = pd.to_numeric(bars.iloc[-1].get("close"), errors="coerce")
    if pd.isna(entry) or pd.isna(exit_price) or float(entry) == 0:
        return None
    move = (float(exit_price) - float(entry)) / float(entry) * 10000
    stop_bps = -200.0
    low = pd.to_numeric(bars.get("low"), errors="coerce").min()
    if pd.notna(low) and (float(low) - float(entry)) / float(entry) * 10000 <= stop_bps:
        return stop_bps
    return float(move)


def build_missed_opportunities(
    *,
    session_date: date,
    intraday_bars: pd.DataFrame,
    universe: pd.DataFrame | None = None,
    signal_log: pd.DataFrame | None = None,
    traded_symbols: set[str] | None = None,
    move_threshold_pct: float = 5.0,
) -> MissedOpportunityReport:
    bars = _session_rows(intraday_bars, session_date)
    if bars.empty or "symbol" not in bars.columns:
        rows: list[dict[str, Any]] = []
        return MissedOpportunityReport(session_date, rows, render_markdown(session_date, rows))
    bars["symbol"] = bars["symbol"].astype(str).str.upper().str.strip()
    logs = _session_rows(signal_log if signal_log is not None else pd.DataFrame(), session_date, timestamp_col="decision_time")
    if not logs.empty and "symbol" in logs.columns:
        logs["symbol"] = logs["symbol"].astype(str).str.upper().str.strip()
    universe_symbols = _symbol_set(universe if universe is not None else pd.DataFrame())
    traded = {symbol.upper().strip() for symbol in (traded_symbols or set()) if symbol}
    rows = []
    for symbol, group in bars.groupby("symbol"):
        if symbol in traded:
            continue
        numeric = group.copy()
        for col in ["open", "high", "low", "close"]:
            if col in numeric.columns:
                numeric[col] = pd.to_numeric(numeric[col], errors="coerce")
        first_open = numeric["open"].dropna().iloc[0] if "open" in numeric.columns and not numeric["open"].dropna().empty else None
        day_high = numeric["high"].max() if "high" in numeric.columns else None
        day_low = numeric["low"].min() if "low" in numeric.columns else None
        if first_open is None or pd.isna(first_open) or float(first_open) == 0:
            continue
        up = (float(day_high) - float(first_open)) / float(first_open) * 100 if pd.notna(day_high) else 0.0
        down = (float(day_low) - float(first_open)) / float(first_open) * 100 if pd.notna(day_low) else 0.0
        move = up if abs(up) >= abs(down) else down
        if abs(move) < move_threshold_pct:
            continue
        symbol_logs = logs[logs["symbol"].eq(symbol)] if not logs.empty and "symbol" in logs.columns else pd.DataFrame()
        in_universe = symbol in universe_symbols if universe_symbols else bool(len(symbol_logs))
        rows.append(
            {
                "session_date": session_date,
                "symbol": symbol,
                "intraday_move_pct": round(float(move), 2),
                "in_universe": in_universe,
                "exclusion_reason": "" if in_universe else "not_in_same_day_universe",
                "signal_log_count": int(len(symbol_logs)),
                "max_continuation_probability": _max_continuation(symbol_logs),
                "first_blocking_gate": _first_blocking_gate(symbol_logs),
                "hypothetical_pnl_bps": _hypothetical_pnl_bps(numeric),
                "details": {
                    "day_open": float(first_open),
                    "day_high": None if pd.isna(day_high) else float(day_high),
                    "day_low": None if pd.isna(day_low) else float(day_low),
                },
            }
        )
    rows = sorted(rows, key=lambda row: abs(float(row["intraday_move_pct"])), reverse=True)
    return MissedOpportunityReport(session_date, rows, render_markdown(session_date, rows))


def render_markdown(session_date: date, rows: list[dict[str, Any]]) -> str:
    lines = [f"# Same-Day Missed Opportunities - {session_date}", ""]
    if not rows:
        lines.append("No missed opportunities above the configured move threshold.")
        return "\n".join(lines) + "\n"
    for row in rows:
        lines.extend(
            [
                f"## {row['symbol']} ({row['intraday_move_pct']:+.2f}%)",
                "",
                f"- In universe: {row['in_universe']}",
                f"- Exclusion reason: {row.get('exclusion_reason') or 'none'}",
                f"- Signal log count: {row['signal_log_count']}",
                f"- Max continuation probability: {row.get('max_continuation_probability')}",
                f"- First blocking gate: {row.get('first_blocking_gate') or 'none'}",
                f"- Hypothetical P&L bps: {row.get('hypothetical_pnl_bps')}",
                "",
            ]
        )
    return "\n".join(lines)


def write_report(report: MissedOpportunityReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{report.session_date}.md"
    path.write_text(report.markdown, encoding="utf-8")
    return path
