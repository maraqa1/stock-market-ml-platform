from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, TRADING_DIR, timestamp
from stockml.diagnostics.broker_fill_reconciliation import latest_file, read_csv

REPORT_COLUMNS = [
    "status",
    "trade_id",
    "position_id",
    "symbol",
    "side",
    "position_status",
    "entry_time",
    "exit_time",
    "holding_minutes",
    "exit_reason",
    "outcome_bucket",
    "pnl_usd",
    "return_pct",
    "management_action_family",
    "lineage_quality",
    "lineage_warnings",
    "diagnostic_note",
]

SUMMARY_COLUMNS = [
    "status",
    "management_action_family",
    "exit_reason",
    "trade_count",
    "winner_count",
    "loser_count",
    "flat_count",
    "open_count",
    "net_pnl_usd",
    "mean_pnl_usd",
    "median_return_pct",
    "mean_holding_minutes",
]

REASON_FAMILIES = {
    "stop_loss": "risk_exit",
    "hard_stop_loss": "risk_exit",
    "take_profit": "profit_exit",
    "trailing_giveback": "profit_protection",
    "trailing_profit_giveback": "profit_protection",
    "stale_signal": "signal_exit",
    "unknown_signal": "signal_exit",
    "defensive_close": "risk_exit",
    "manual_close": "manual_exit",
    "snapshot_flattened": "reconstructed_exit",
    "open": "open_position",
    "": "unknown",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class PositionManagementOutcomesResult:
    frame: pd.DataFrame
    summary_frame: pd.DataFrame
    summary: dict[str, Any]
    report_path: Path | None = None
    summary_csv_path: Path | None = None
    summary_path: Path | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def _num(value: Any, default: float = 0.0) -> float:
    text = _text(value)
    if not text:
        return default
    try:
        return float(text.replace(",", ""))
    except Exception:
        return default


def _pnl(row: dict[str, Any]) -> float:
    for column in ["realised_pnl", "realized_pnl", "unrealised_pnl", "unrealized_pnl", "realized_pnl_usd"]:
        if _text(row.get(column)):
            return _num(row.get(column), 0.0)
    return 0.0


def _return(row: dict[str, Any]) -> float:
    for column in ["realised_return_pct", "realized_return_pct", "unrealised_return_pct", "unrealized_return_pct"]:
        if _text(row.get(column)):
            return _num(row.get(column), 0.0)
    entry = _num(row.get("entry_price"), 0.0)
    qty = _num(row.get("entry_quantity"), 0.0)
    basis = abs(entry * qty)
    return (_pnl(row) / basis * 100.0) if basis else 0.0


def _exit_reason(row: dict[str, Any]) -> str:
    status = _text(row.get("position_status")).lower()
    reason = _text(row.get("exit_reason") or row.get("close_reason") or row.get("trigger_source")).lower()
    if not reason and status == "open":
        return "open"
    return reason or "unknown"


def _bucket(status: str, pnl: float) -> str:
    if status == "open":
        return "open_winner" if pnl > 0 else "open_loser" if pnl < 0 else "open_flat"
    if pnl > 0:
        return "winner"
    if pnl < 0:
        return "loser"
    return "flat"


def _family(reason: str) -> str:
    return REASON_FAMILIES.get(reason, "other_exit")


def build_position_management_outcomes(ledger: pd.DataFrame) -> PositionManagementOutcomesResult:
    if ledger.empty:
        frame = pd.DataFrame([{"status": "insufficient_data", "diagnostic_note": "trade_ledger_missing_or_empty"}], columns=REPORT_COLUMNS)
        summary_frame = pd.DataFrame([{"status": "insufficient_data", "management_action_family": "insufficient_data", "exit_reason": "trade_ledger_missing_or_empty", "trade_count": 0}], columns=SUMMARY_COLUMNS)
        return PositionManagementOutcomesResult(frame, summary_frame, summarize(frame, summary_frame))
    rows: list[dict[str, Any]] = []
    for row in ledger.fillna("").to_dict("records"):
        status = _text(row.get("position_status")).lower() or "unknown"
        reason = _exit_reason(row)
        pnl = _pnl(row)
        ret = _return(row)
        rows.append(
            {
                "status": "ok",
                "trade_id": _text(row.get("trade_id")),
                "position_id": _text(row.get("position_id")),
                "symbol": _text(row.get("symbol") or row.get("ticker")).upper(),
                "side": _text(row.get("side")),
                "position_status": status,
                "entry_time": _text(row.get("entry_time") or row.get("opened_at")),
                "exit_time": _text(row.get("exit_time") or row.get("closed_at")),
                "holding_minutes": _num(row.get("holding_minutes"), 0.0),
                "exit_reason": reason,
                "outcome_bucket": _bucket(status, pnl),
                "pnl_usd": pnl,
                "return_pct": ret,
                "management_action_family": _family(reason),
                "lineage_quality": _text(row.get("lineage_quality")),
                "lineage_warnings": _text(row.get("lineage_warnings")),
                "diagnostic_note": "read_only_position_outcome",
            }
        )
    frame = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    summary_frame = aggregate_outcomes(frame)
    return PositionManagementOutcomesResult(frame, summary_frame, summarize(frame, summary_frame))


def aggregate_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or frame["status"].astype(str).eq("insufficient_data").all():
        return pd.DataFrame([{"status": "insufficient_data", "management_action_family": "insufficient_data", "exit_reason": "trade_ledger_missing_or_empty", "trade_count": 0}], columns=SUMMARY_COLUMNS)
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(["management_action_family", "exit_reason"], dropna=False):
        family, reason = keys
        pnl = pd.to_numeric(group["pnl_usd"], errors="coerce").fillna(0.0)
        returns = pd.to_numeric(group["return_pct"], errors="coerce")
        holding = pd.to_numeric(group["holding_minutes"], errors="coerce")
        buckets = group["outcome_bucket"].astype(str)
        rows.append(
            {
                "status": "ok",
                "management_action_family": family,
                "exit_reason": reason,
                "trade_count": int(len(group)),
                "winner_count": int(buckets.isin(["winner", "open_winner"]).sum()),
                "loser_count": int(buckets.isin(["loser", "open_loser"]).sum()),
                "flat_count": int(buckets.isin(["flat", "open_flat"]).sum()),
                "open_count": int(group["position_status"].astype(str).eq("open").sum()),
                "net_pnl_usd": float(pnl.sum()),
                "mean_pnl_usd": float(pnl.mean()) if len(pnl) else 0.0,
                "median_return_pct": float(returns.median()) if returns.notna().any() else 0.0,
                "mean_holding_minutes": float(holding.mean()) if holding.notna().any() else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).sort_values(["net_pnl_usd", "trade_count"], ascending=[True, False]).reset_index(drop=True)


def summarize(frame: pd.DataFrame, summary_frame: pd.DataFrame) -> dict[str, Any]:
    status = "insufficient_data" if not frame.empty and frame["status"].astype(str).eq("insufficient_data").all() else "ok"
    pnl = pd.to_numeric(frame.get("pnl_usd", pd.Series(dtype=float)), errors="coerce").fillna(0.0) if not frame.empty else pd.Series(dtype=float)
    buckets = frame.get("outcome_bucket", pd.Series(dtype=str)).astype(str) if not frame.empty else pd.Series(dtype=str)
    return {
        "status": status,
        "trade_rows": int(0 if status == "insufficient_data" else len(frame)),
        "summary_rows": int(len(summary_frame)),
        "winner_rows": int(buckets.isin(["winner", "open_winner"]).sum()),
        "loser_rows": int(buckets.isin(["loser", "open_loser"]).sum()),
        "open_rows": int(frame.get("position_status", pd.Series(dtype=str)).astype(str).eq("open").sum()) if not frame.empty else 0,
        "net_pnl_usd": float(pnl.sum()) if len(pnl) else 0.0,
        "worst_family": str(summary_frame.iloc[0].get("management_action_family", "")) if not summary_frame.empty else "",
    }


def latest_inputs(root: Path = PROJECT_ROOT) -> pd.DataFrame:
    diagnostics = root / "data" / "trading" / "diagnostics"
    ledger = read_csv(latest_file(diagnostics, "trade_ledger_*.csv"))
    if ledger.empty:
        closed = read_csv(latest_file(root / "data" / "trading", "closed_trades_attribution_*.csv"))
        if not closed.empty:
            closed = closed.rename(columns={"direction": "side", "opened_at": "entry_time", "closed_at": "exit_time", "realized_pnl_usd": "realised_pnl"})
            closed["position_status"] = "closed"
            ledger = closed
    return ledger


def build_latest_position_management_outcomes(root: Path = PROJECT_ROOT) -> PositionManagementOutcomesResult:
    return build_position_management_outcomes(latest_inputs(root))


def write_position_management_outcomes(result: PositionManagementOutcomesResult, output_dir: Path | str = TRADING_DIR / "diagnostics") -> PositionManagementOutcomesResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    report = out / f"position_management_outcomes_{stamp}.csv"
    summary_csv = out / f"position_management_outcomes_summary_{stamp}.csv"
    summary_md = out / f"position_management_outcomes_summary_{stamp}.md"
    result.frame.to_csv(report, index=False)
    result.summary_frame.to_csv(summary_csv, index=False)
    summary_md.write_text("# Position Management Outcomes Diagnostic\n\n" + "\n".join(f"- {key}: {value}" for key, value in result.summary.items()) + "\n\nThis report is read-only and does not alter position-management rules.\n", encoding="utf-8")
    return PositionManagementOutcomesResult(result.frame, result.summary_frame, result.summary, report, summary_csv, summary_md)
