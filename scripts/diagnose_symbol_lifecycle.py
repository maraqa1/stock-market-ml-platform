from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from stockml.autopilot.eod import eod_flatten_window_active
from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.db.connection import get_engine


OUTPUT_COLUMNS = [
    "symbol",
    "trade_id",
    "candidate_id",
    "signal_id",
    "open_time",
    "close_time",
    "hold_minutes",
    "open_price",
    "close_price",
    "realized_pnl",
    "close_reason",
    "close_reason_valid",
    "eod_window_active",
    "minimum_hold_pass",
    "cooldown_pass",
    "reopen_count_today",
    "open_allowed_by_lifecycle_guard",
    "close_allowed_by_lifecycle_guard",
    "block_reason",
    "order_source",
    "session_mode",
]


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        parsed = float(value)
        if pd.isna(parsed):
            return default
        return parsed
    except Exception:
        return default


def _read_closed(symbol: str, day: str, root: Path) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for path in sorted((root / "data" / "trading").glob("closed_trades_attribution_*.csv")):
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        if frame.empty or "symbol" not in frame.columns:
            continue
        frame = frame[frame["symbol"].astype(str).str.upper().eq(symbol)]
        if frame.empty:
            continue
        if "opened_at" in frame.columns:
            opened = pd.to_datetime(frame["opened_at"], errors="coerce", utc=True)
            frame = frame[opened.dt.strftime("%Y-%m-%d").eq(day)]
        if frame.empty:
            continue
        frame["__source_file"] = str(path)
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    keys = [column for column in ["position_id", "symbol", "opened_at", "closed_at", "direction"] if column in out.columns]
    if keys:
        out = out.drop_duplicates(subset=keys)
    return out.sort_values([column for column in ["opened_at", "closed_at"] if column in out.columns], kind="stable")


def _open_log(symbol: str, day: str) -> pd.DataFrame:
    engine = get_engine(required=False)
    if engine is None:
        return pd.DataFrame()
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    end = start + pd.Timedelta(days=1)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                select logged_at, symbol, verdict, block_reason, order_id, details
                from autopilot_open_log
                where symbol = :symbol
                  and logged_at >= :start
                  and logged_at < :end
                order by logged_at
                """
            ),
            {"symbol": symbol, "start": start, "end": end},
        ).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def build_symbol_lifecycle(symbol: str, day: str, *, root: Path = PROJECT_ROOT, minimum_hold_minutes: int = 30, cooldown_minutes: int = 60) -> pd.DataFrame:
    clean = symbol.upper().strip()
    closed = _read_closed(clean, day, root)
    logs = _open_log(clean, day)
    opened_times = []
    if not logs.empty and "verdict" in logs.columns:
        opened_times = [_dt(value) for value in logs[logs["verdict"].astype(str).eq("opened")]["logged_at"].tolist()]
        opened_times = [value for value in opened_times if value is not None]

    rows: list[dict[str, Any]] = []
    reopen_count = 0
    previous_close: datetime | None = None
    for _, trade in closed.iterrows():
        open_time = _dt(trade.get("opened_at"))
        close_time = _dt(trade.get("closed_at"))
        hold = ((close_time - open_time).total_seconds() / 60.0) if open_time and close_time else None
        reason = str(trade.get("close_reason") or "unknown_close_reason")
        eod_active = bool(close_time and eod_flatten_window_active(close_time))
        eod_invalid = reason.upper() == "EOD_FLATTEN" and not eod_active
        minimum_pass = hold is None or hold >= minimum_hold_minutes or reason.lower() in {
            "hard_stop_hit",
            "take_profit_hit",
            "manual_kill",
            "broker_error_correction",
            "duplicate_order_correction",
            "emergency_risk_breach",
        }
        cooldown_pass = previous_close is None or open_time is None or ((open_time - previous_close).total_seconds() / 60.0) >= cooldown_minutes
        if previous_close is not None and open_time is not None:
            reopen_count += 1
        block_reasons = []
        if eod_invalid:
            block_reasons.append("eod_flatten_outside_window")
        if not minimum_pass:
            block_reasons.append("minimum_hold_period_not_met")
        if not cooldown_pass:
            block_reasons.append("cooldown_after_close_active")
        rows.append(
            {
                "symbol": clean,
                "trade_id": trade.get("trade_id", ""),
                "candidate_id": trade.get("candidate_id", ""),
                "signal_id": trade.get("opened_by_signal_id", ""),
                "open_time": open_time.isoformat() if open_time else "",
                "close_time": close_time.isoformat() if close_time else "",
                "hold_minutes": round(hold, 2) if hold is not None else "",
                "open_price": trade.get("entry_fill", ""),
                "close_price": trade.get("exit_fill", ""),
                "realized_pnl": trade.get("realized_pnl_usd", ""),
                "close_reason": reason,
                "close_reason_valid": not eod_invalid,
                "eod_window_active": eod_active,
                "minimum_hold_pass": minimum_pass,
                "cooldown_pass": cooldown_pass,
                "reopen_count_today": reopen_count,
                "open_allowed_by_lifecycle_guard": cooldown_pass,
                "close_allowed_by_lifecycle_guard": not block_reasons,
                "block_reason": ";".join(block_reasons),
                "order_source": "paper_autopilot" if opened_times else "unknown",
                "session_mode": "regular_session",
            }
        )
        if close_time:
            previous_close = close_time
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def write_outputs(frame: pd.DataFrame, *, symbol: str, root: Path = PROJECT_ROOT, stamp: str | None = None) -> tuple[Path, Path]:
    out_dir = root / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_stamp = stamp or timestamp()
    csv_path = out_dir / f"symbol_lifecycle_{symbol.upper()}_{clean_stamp}.csv"
    md_path = out_dir / f"symbol_lifecycle_{symbol.upper()}_{clean_stamp}.md"
    frame.to_csv(csv_path, index=False)
    pnl = pd.to_numeric(frame.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0)
    hold = pd.to_numeric(frame.get("hold_minutes", pd.Series(dtype=float)), errors="coerce")
    outside = int((frame.get("block_reason", pd.Series(dtype=str)).astype(str).str.contains("eod_flatten_outside_window", na=False)).sum()) if not frame.empty else 0
    before_hold = int((hold < 30).fillna(False).sum()) if not frame.empty else 0
    churn = bool(len(frame) > 1 or outside > 0 or before_hold > 0)
    md_path.write_text(
        "\n".join(
            [
                f"# Symbol Lifecycle Diagnostic: {symbol.upper()}",
                "",
                f"- total_opens: {len(frame)}",
                f"- total_closes: {len(frame)}",
                f"- reopen_count: {max(0, len(frame) - 1)}",
                f"- closes_before_minimum_hold: {before_hold}",
                f"- eod_flatten_outside_window_count: {outside}",
                f"- realized_pnl: {round(float(pnl.sum()), 4)}",
                f"- churn_detected: {'yes' if churn else 'no'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return csv_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    frame = build_symbol_lifecycle(args.symbol, args.date)
    csv_path, md_path = write_outputs(frame, symbol=args.symbol)
    outside = int(frame.get("block_reason", pd.Series(dtype=str)).astype(str).str.contains("eod_flatten_outside_window", na=False).sum()) if not frame.empty else 0
    print(f"symbol_lifecycle_status: {'churn_detected' if len(frame) > 1 else 'ok'}")
    print(f"rows: {len(frame)}")
    print(f"eod_flatten_outside_window_count: {outside}")
    print(f"csv_path: {csv_path}")
    print(f"markdown_path: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
