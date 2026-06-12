from __future__ import annotations

import csv
import hashlib
import re
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from stockml.common.paths import TRADING_DIR
from stockml.db.connection import get_engine
from stockml.db.schema import closed_trades_attribution
from stockml.reports.closed_trade_metrics import (
    as_datetime,
    as_float,
    classify_close_reason,
    exit_slippage_bps,
    mfe_mae_metrics,
    modeled_costs_bps,
    normalize_direction,
    signal_to_entry_bps,
    signed_price_move_bps,
)


POSITION_SNAPSHOT_PATTERN = re.compile(r"08_alpaca_paper_positions_(\d{8}_\d{6})\.csv$")


def build_closed_trades_from_position_snapshots(
    *,
    root: Path | str | None = None,
    max_snapshots: int = 2000,
    created_at: datetime | None = None,
) -> pd.DataFrame:
    """Reconstruct closed paper trades from broker position snapshots.

    Alpaca position snapshots only contain currently open positions. When a symbol is
    present in one snapshot and missing in a later snapshot, the position has been
    flattened. If no exact close-fill activity is available, use the last observed
    broker mark as an estimated exit fill and mark the trigger source accordingly.
    """
    project_root = Path(root) if root is not None else Path.cwd()
    portal_dir = project_root / "data" / "portal_outputs" if (project_root / "data").exists() else project_root
    paths = sorted(portal_dir.glob("08_alpaca_paper_positions_*.csv"), key=_snapshot_time)
    if max_snapshots > 0:
        paths = paths[-max_snapshots:]
    active: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    for path in paths:
        snap_time = _snapshot_time(path)
        frame = _read_position_snapshot(path)
        current: dict[str, dict[str, Any]] = {}
        for record in frame.to_dict("records"):
            symbol = str(record.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            normalized = _snapshot_record(record, snap_time)
            previous = active.get(symbol)
            if previous:
                normalized["opened_at"] = previous.get("opened_at") or normalized.get("opened_at")
                normalized["entry_fill"] = previous.get("entry_fill") or normalized.get("entry_fill")
                normalized["entry_target"] = previous.get("entry_target") or normalized.get("entry_target")
                normalized["signal_price"] = previous.get("signal_price") or normalized.get("signal_price")
                normalized["position_id"] = previous.get("position_id") or normalized.get("position_id")
            current[symbol] = normalized
        for symbol, previous in list(active.items()):
            if symbol not in current:
                closed.append(_closed_trade_from_snapshot(previous, closed_at=snap_time))
        active = current
    if not closed:
        return pd.DataFrame(columns=ATTRIBUTION_COLUMNS)
    frame = build_closed_trades_attribution(closed, created_at=created_at)
    if frame.empty:
        return frame
    return frame.drop_duplicates("position_id", keep="last").reset_index(drop=True)


def write_reconstructed_closed_trades_attribution(
    *,
    root: Path | str | None = None,
    stamp: str | None = None,
    output_dir: Path | None = None,
    persist: bool = False,
    engine: Engine | None = None,
) -> tuple[pd.DataFrame, Path]:
    frame = build_closed_trades_from_position_snapshots(root=root)
    base = Path(root) if root is not None else Path.cwd()
    out_dir = output_dir or ((base / "data" / "trading") if (base / "data").exists() else TRADING_DIR)
    path = write_attribution_csv(frame, stamp=stamp, output_dir=out_dir)
    if persist and not frame.empty:
        persist_attribution(frame, engine=engine)
    return frame, path


def _snapshot_time(path: Path) -> datetime:
    match = POSITION_SNAPSHOT_PATTERN.search(path.name)
    if match:
        parsed = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
        return parsed.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _read_position_snapshot(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 1:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _snapshot_record(record: dict[str, Any], snap_time: datetime) -> dict[str, Any]:
    side = normalize_direction(record.get("side") or ("short" if as_float(record.get("qty")) < 0 else "long"))
    entry = as_float(_first(record, "avg_entry_price", "entry_fill", default=0.0))
    current = as_float(_first(record, "current_price", "last", "market_price", default=entry))
    qty = abs(as_float(record.get("qty")))
    return {
        "position_id": _snapshot_position_id(str(record.get("symbol") or ""), snap_time.isoformat(), side, qty, entry),
        "symbol": str(record.get("symbol") or "").strip().upper(),
        "direction": side,
        "opened_at": snap_time.isoformat(),
        "closed_at": "",
        "signal_price": entry,
        "entry_target": entry,
        "entry_fill": entry,
        "exit_target": current,
        "exit_fill": current,
        "quantity": qty,
        "close_reason": "snapshot_flattened",
        "trigger_source": "position_snapshot_reconstruction",
        "signal_state_at_close": "estimated_exit_from_last_position_snapshot",
    }


def _closed_trade_from_snapshot(record: dict[str, Any], *, closed_at: datetime) -> dict[str, Any]:
    out = dict(record)
    out["closed_at"] = closed_at.isoformat()
    out["close_reason"] = "snapshot_flattened"
    out["trigger_source"] = "position_snapshot_reconstruction"
    out["signal_state_at_close"] = "estimated_exit_from_last_position_snapshot"
    return out


def _snapshot_position_id(symbol: str, opened_at: str, direction: str, qty: float, entry_fill: float) -> int:
    key = f"{symbol.upper()}|{opened_at}|{direction}|{qty:.8f}|{entry_fill:.8f}"
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:15], 16)

ATTRIBUTION_COLUMNS = [
    "position_id",
    "symbol",
    "strategy_stream",
    "direction",
    "opened_at",
    "closed_at",
    "opened_by_signal_id",
    "signal_price",
    "entry_target",
    "entry_fill",
    "exit_target",
    "exit_fill",
    "signal_to_entry_bps",
    "entry_to_exit_bps",
    "exit_slippage_bps",
    "modeled_costs_bps",
    "realized_net_bps",
    "realized_pnl_usd",
    "max_favourable_bps",
    "max_adverse_bps",
    "minutes_to_first_positive",
    "minutes_to_max_adverse",
    "close_reason",
    "trigger_source",
    "signal_state_at_close",
    "created_at",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_attribution_row(
    trade: dict[str, Any] | pd.Series,
    *,
    bars: pd.DataFrame | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    row = dict(trade)
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    direction = normalize_direction(_first(row, "direction", "side", "trade_action", default="long"))
    entry_fill = as_float(_first(row, "entry_fill", "avg_entry_price", "entry_price", default=0.0))
    exit_fill = as_float(_first(row, "exit_fill", "exit_price", "filled_avg_price", default=0.0))
    signal_price = as_float(_first(row, "signal_price", "close_at_signal", "model_price", default=entry_fill))
    entry_target = as_float(_first(row, "entry_target", "limit_price", default=entry_fill))
    exit_target = as_float(_first(row, "exit_target", "close_target", default=exit_fill))
    quantity = abs(as_float(_first(row, "quantity", "qty", "filled_qty", default=0.0)))
    entry_notional = entry_fill * quantity
    cost_bps = modeled_costs_bps(_first(row, "half_spread_at_entry", "half_spread_bps", default=0.0))
    entry_to_exit = signed_price_move_bps(entry_fill, exit_fill, direction)
    net_bps = entry_to_exit - cost_bps
    mfe_mae = mfe_mae_metrics(bars, entry_fill=entry_fill, direction=direction, opened_at=_first(row, "opened_at", "entry_at", "filled_at"))
    close_reason = classify_close_reason(_first(row, "close_reason", "exit_reason", "trigger", default=""), details)
    created = created_at or utc_now()
    opened_at = as_datetime(_first(row, "opened_at", "entry_at", "filled_at"))
    closed_at = as_datetime(_first(row, "closed_at", "exit_at", "closed_time"))
    payload = {
        "position_id": _first(row, "position_id", "id", default=""),
        "symbol": str(_first(row, "symbol", "ticker", default="")).upper(),
        "strategy_stream": _normal_stream(_first(row, "strategy_stream", default="multi_day_forecast")),
        "direction": direction,
        "opened_at": opened_at.isoformat() if opened_at else "",
        "closed_at": closed_at.isoformat() if closed_at else "",
        "opened_by_signal_id": _first(row, "opened_by_signal_id", "signal_id", default=""),
        "signal_price": round(signal_price, 6),
        "entry_target": round(entry_target, 6),
        "entry_fill": round(entry_fill, 6),
        "exit_target": round(exit_target, 6),
        "exit_fill": round(exit_fill, 6),
        "signal_to_entry_bps": round(signal_to_entry_bps(signal_price, entry_fill, direction), 4),
        "entry_to_exit_bps": round(entry_to_exit, 4),
        "exit_slippage_bps": round(exit_slippage_bps(exit_target, exit_fill, direction), 4),
        "modeled_costs_bps": round(cost_bps, 4),
        "realized_net_bps": round(net_bps, 4),
        "realized_pnl_usd": round(entry_notional * net_bps / 10000.0, 4),
        "max_favourable_bps": mfe_mae["max_favourable_bps"],
        "max_adverse_bps": mfe_mae["max_adverse_bps"],
        "minutes_to_first_positive": mfe_mae["minutes_to_first_positive"],
        "minutes_to_max_adverse": mfe_mae["minutes_to_max_adverse"],
        "close_reason": close_reason,
        "trigger_source": _first(row, "trigger_source", "source", default=""),
        "signal_state_at_close": _first(row, "signal_state_at_close", "signal_state", default=""),
        "created_at": created.isoformat(),
    }
    return {column: payload.get(column) for column in ATTRIBUTION_COLUMNS}


def build_closed_trades_attribution(
    trades: pd.DataFrame | list[dict[str, Any]],
    *,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    created_at: datetime | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(trades)
    if frame.empty:
        return pd.DataFrame(columns=ATTRIBUTION_COLUMNS)
    bars_by_symbol = bars_by_symbol or {}
    rows = []
    for _, trade in frame.iterrows():
        symbol = str(_first(dict(trade), "symbol", "ticker", default="")).upper()
        rows.append(build_attribution_row(trade, bars=bars_by_symbol.get(symbol), created_at=created_at))
    out = pd.DataFrame(rows, columns=ATTRIBUTION_COLUMNS)
    return out.sort_values(["closed_at", "position_id"], kind="stable").reset_index(drop=True)


def attribution_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "mean_realized_net_bps": 0.0,
            "median_realized_net_bps": 0.0,
            "mean_mfe_capture_ratio": 0.0,
            "stop_loss_count": 0,
            "negative_but_mfe_positive_count": 0,
            "verdict": "INSUFFICIENT_DATA",
        }
    net = pd.to_numeric(frame["realized_net_bps"], errors="coerce")
    mfe = pd.to_numeric(frame["max_favourable_bps"], errors="coerce")
    capture = net / mfe.where(mfe > 0)
    negative_after_positive = frame[(net < 0) & (mfe > 0)]
    return {
        "trade_count": int(len(frame)),
        "win_rate": round(float((net > 0).mean()), 4),
        "mean_realized_net_bps": round(float(net.mean()), 4),
        "median_realized_net_bps": round(float(net.median()), 4),
        "mean_mfe_capture_ratio": round(float(capture.dropna().mean()), 4) if not capture.dropna().empty else 0.0,
        "stop_loss_count": int((frame["close_reason"] == "STOP_LOSS").sum()),
        "negative_but_mfe_positive_count": int(len(negative_after_positive)),
        "verdict": attribution_verdict(frame),
    }


def attribution_verdict(frame: pd.DataFrame) -> str:
    if frame.empty or len(frame) < 5:
        return "INSUFFICIENT_DATA"
    net = pd.to_numeric(frame["realized_net_bps"], errors="coerce")
    mfe = pd.to_numeric(frame["max_favourable_bps"], errors="coerce")
    mae = pd.to_numeric(frame["max_adverse_bps"], errors="coerce")
    stop_loss_rate = float((frame["close_reason"] == "STOP_LOSS").mean())
    negative_after_positive_rate = float(((net < 0) & (mfe > 25)).mean())
    early_adverse = float((mae < -75).mean())
    if net.mean() > 0 and stop_loss_rate < 0.35:
        return "PROCESS_OK"
    if early_adverse > 0.5:
        return "ENTRY_TIMING_PROBLEM"
    if negative_after_positive_rate > 0.35:
        return "EXIT_MANAGEMENT_PROBLEM"
    if stop_loss_rate > 0.5:
        return "STOP_TOO_TIGHT_OR_SIGNAL_WEAK"
    return "MIXED_REVIEW_REQUIRED"


def section_rows(frame: pd.DataFrame, by: str) -> list[dict[str, Any]]:
    if frame.empty or by not in frame.columns:
        return []
    rows = []
    for key, group in frame.groupby(by, dropna=False):
        summary = attribution_summary(group)
        rows.append({by: key, **summary})
    return rows


def csv_text(frame: pd.DataFrame) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=ATTRIBUTION_COLUMNS)
    writer.writeheader()
    for row in frame.fillna("").to_dict("records"):
        writer.writerow({column: row.get(column, "") for column in ATTRIBUTION_COLUMNS})
    return out.getvalue()


def write_attribution_csv(frame: pd.DataFrame, *, stamp: str | None = None, output_dir: Path | None = None) -> Path:
    stamp = stamp or utc_now().strftime("%Y%m%d_%H%M%S")
    output_dir = output_dir or TRADING_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"closed_trades_attribution_{stamp}.csv"
    frame.to_csv(path, index=False)
    return path


def persist_attribution(frame: pd.DataFrame, *, engine: Engine | None = None) -> int:
    if frame.empty:
        return 0
    db = engine or get_engine(required=True)
    rows = [_db_row(row) for row in frame.to_dict("records")]
    with db.begin() as conn:
        for row in rows:
            conn.execute(delete(closed_trades_attribution).where(closed_trades_attribution.c.position_id == row["position_id"]))
        conn.execute(insert(closed_trades_attribution), rows)
    return len(rows)


def load_attribution(*, engine: Engine | None = None, limit: int = 500) -> pd.DataFrame:
    try:
        db = engine or get_engine(required=False)
        if db is None:
            return pd.DataFrame(columns=ATTRIBUTION_COLUMNS)
        with db.connect() as conn:
            rows = (
                conn.execute(
                    select(closed_trades_attribution)
                    .order_by(closed_trades_attribution.c.closed_at.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=ATTRIBUTION_COLUMNS)


def latest_attribution_file(root: Path | None = None) -> Path | None:
    base = Path(root) if root else TRADING_DIR
    trading_dir = base / "data" / "trading" if (base / "data").exists() else base
    files = sorted(trading_dir.glob("closed_trades_attribution_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _db_row(row: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(row)
    parsed["position_id"] = int(as_float(parsed.get("position_id")))
    for column in ["opened_at", "closed_at", "created_at"]:
        parsed[column] = as_datetime(parsed.get(column))
    return parsed


def _first(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in {None, ""}:
            return value
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    for key in keys:
        value = details.get(key)
        if value not in {None, ""}:
            return value
    return default


def _normal_stream(value: Any) -> str:
    text = str(value or "multi_day_forecast").strip().lower()
    if text in {"same_day", "same_day_momentum"}:
        return "same_day_momentum"
    return "multi_day_forecast"
