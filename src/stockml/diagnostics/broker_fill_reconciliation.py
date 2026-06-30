from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, TRADING_DIR, timestamp

RECON_COLUMNS = [
    "client_order_id", "broker_order_id", "symbol", "side", "planned_status", "result_status", "tracking_status",
    "activity_submitted_events", "activity_filled_events", "ledger_trades", "filled_qty", "filled_avg_price",
    "reconciliation_status", "reconciliation_warning", "suggested_action",
]

STATUS_SUBMITTED = {"submitted", "accepted", "new", "partially_filled", "filled"}
STATUS_FILLED = {"filled"}
STATUS_DRY = {"dry_run", "plan_only"}
STATUS_REJECTED = {"rejected", "error", "canceled", "cancelled", "expired"}


@dataclass(frozen=True)
class ReconciliationResult:
    frame: pd.DataFrame
    summary: dict[str, Any]
    report_path: Path | None = None
    summary_path: Path | None = None


def latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    files = [path for path in directory.glob(pattern) if path.is_file()]
    return max(files, key=lambda path: (path.stat().st_mtime, path.name)) if files else None


def read_csv(path: Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def _num(value: Any) -> float:
    text = _text(value)
    if not text:
        return 0.0
    try:
        return float(text.replace(",", ""))
    except Exception:
        return 0.0


def _status(value: Any) -> str:
    return _text(value).lower()


def _order_key(row: pd.Series | dict[str, Any]) -> str:
    client = _text(row.get("client_order_id"))
    broker = _text(row.get("broker_order_id") or row.get("order_id"))
    symbol = _text(row.get("symbol") or row.get("ticker")).upper()
    side = _text(row.get("side")).lower()
    return client or broker or (f"{symbol}:{side}" if symbol and side else symbol)


def _index_latest(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in frame.fillna("").to_dict("records"):
        key = _order_key(row)
        if key:
            out[key] = row
    return out


def _activity_counts(activity: pd.DataFrame) -> tuple[dict[str, int], dict[str, int]]:
    submitted: dict[str, int] = {}
    filled: dict[str, int] = {}
    if activity.empty:
        return submitted, filled
    for row in activity.fillna("").to_dict("records"):
        key = _order_key(row)
        if not key:
            continue
        event_type = _status(row.get("event_type"))
        if event_type in {"submitted", "order_submitted", "candidate_submitted"}:
            submitted[key] = submitted.get(key, 0) + 1
        if event_type in {"filled", "close_filled"}:
            filled[key] = filled.get(key, 0) + 1
    return submitted, filled


def _ledger_counts(ledger: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    if ledger.empty:
        return counts
    for row in ledger.fillna("").to_dict("records"):
        for key in {_order_key(row), _text(row.get("entry_broker_order_id")), _text(row.get("exit_broker_order_id"))}:
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def _decision(result_status: str, tracking_status: str, activity_fills: int, ledger_trades: int, filled_qty: float) -> tuple[str, str, str]:
    statuses = {result_status, tracking_status} - {""}
    if statuses & STATUS_DRY:
        return "dry_run", "plan-only order; no broker fill expected", "none"
    if statuses & STATUS_REJECTED:
        return "terminal_no_fill", "broker/order terminal status without fill", "none"
    broker_filled = bool((statuses & STATUS_FILLED) or filled_qty > 0)
    if broker_filled and activity_fills == 0:
        return "missing_activity_fill", "broker fill exists but activity journal has no fill event", "repair activity journal fill logging"
    if broker_filled and ledger_trades == 0:
        return "missing_ledger_trade", "fill event exists but trade ledger did not build a trade", "repair lifecycle identifiers or ledger matching"
    if broker_filled:
        return "matched_fill", "broker fill, activity fill, and ledger trade are linked", "none"
    if statuses & STATUS_SUBMITTED:
        return "submitted_not_filled", "order is live/accepted/new but not filled yet", "monitor broker status"
    return "not_submitted", "no broker submission evidence", "none"


def reconcile_orders(
    *,
    plan: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
    tracking: pd.DataFrame | None = None,
    activity: pd.DataFrame | None = None,
    ledger: pd.DataFrame | None = None,
) -> ReconciliationResult:
    plan = plan if plan is not None else pd.DataFrame()
    results = results if results is not None else pd.DataFrame()
    tracking = tracking if tracking is not None else pd.DataFrame()
    activity = activity if activity is not None else pd.DataFrame()
    ledger = ledger if ledger is not None else pd.DataFrame()

    result_idx = _index_latest(results)
    tracking_idx = _index_latest(tracking)
    submitted_counts, fill_counts = _activity_counts(activity)
    ledger_counts = _ledger_counts(ledger)
    keys = set(result_idx) | set(tracking_idx) | set(submitted_counts) | set(fill_counts) | set(ledger_counts)
    if not plan.empty:
        keys |= {_order_key(row) for row in plan.fillna("").to_dict("records") if _order_key(row)}

    rows = []
    for key in sorted(keys):
        prow = next((row for row in plan.fillna("").to_dict("records") if _order_key(row) == key), {}) if not plan.empty else {}
        rrow = result_idx.get(key, {})
        trow = tracking_idx.get(key, {})
        source = rrow or trow or prow
        result_status = _status(rrow.get("alpaca_status") or rrow.get("status"))
        tracking_status = _status(trow.get("alpaca_status") or trow.get("status"))
        filled_qty = max(_num(rrow.get("filled_qty")), _num(trow.get("filled_qty")))
        filled_avg_price = max(_num(rrow.get("filled_avg_price")), _num(trow.get("filled_avg_price")))
        status, warning, action = _decision(result_status, tracking_status, fill_counts.get(key, 0), ledger_counts.get(key, 0), filled_qty)
        rows.append({
            "client_order_id": _text(source.get("client_order_id")),
            "broker_order_id": _text(source.get("broker_order_id") or source.get("order_id")),
            "symbol": _text(source.get("symbol") or source.get("ticker")).upper(),
            "side": _text(source.get("side")),
            "planned_status": _text(prow.get("trade_quality_status") or prow.get("candidate_status")),
            "result_status": result_status,
            "tracking_status": tracking_status,
            "activity_submitted_events": submitted_counts.get(key, 0),
            "activity_filled_events": fill_counts.get(key, 0),
            "ledger_trades": ledger_counts.get(key, 0),
            "filled_qty": filled_qty,
            "filled_avg_price": filled_avg_price,
            "reconciliation_status": status,
            "reconciliation_warning": warning,
            "suggested_action": action,
        })
    frame = pd.DataFrame(rows, columns=RECON_COLUMNS)
    summary = summarize(frame)
    return ReconciliationResult(frame=frame, summary=summary)


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"orders": 0, "matched_fills": 0, "missing_activity_fills": 0, "missing_ledger_trades": 0, "submitted_not_filled": 0, "status": "insufficient_data"}
    counts = frame["reconciliation_status"].value_counts().to_dict()
    blocking = int(counts.get("missing_activity_fill", 0) + counts.get("missing_ledger_trade", 0))
    return {
        "orders": int(len(frame)),
        "matched_fills": int(counts.get("matched_fill", 0)),
        "missing_activity_fills": int(counts.get("missing_activity_fill", 0)),
        "missing_ledger_trades": int(counts.get("missing_ledger_trade", 0)),
        "submitted_not_filled": int(counts.get("submitted_not_filled", 0)),
        "status": "ok" if blocking == 0 else "needs_repair",
    }


def latest_reconciliation_inputs(root: Path = PROJECT_ROOT) -> dict[str, pd.DataFrame]:
    portal = root / "data" / "portal_outputs"
    diagnostics = root / "data" / "trading" / "diagnostics"
    return {
        "plan": read_csv(latest_file(portal, "08_alpaca_paper_order_plan_*.csv")),
        "results": read_csv(latest_file(portal, "08_alpaca_paper_order_results_*.csv")),
        "tracking": read_csv(latest_file(portal, "08_alpaca_paper_order_tracking_*.csv")),
        "activity": read_csv(latest_file(root / "data" / "trading" / "exports", "activity_journal_*.csv")),
        "ledger": read_csv(latest_file(diagnostics, "trade_ledger_*.csv")),
    }


def build_latest_reconciliation(root: Path = PROJECT_ROOT) -> ReconciliationResult:
    inputs = latest_reconciliation_inputs(root)
    return reconcile_orders(**inputs)


def write_reconciliation(result: ReconciliationResult, output_dir: Path | str = TRADING_DIR / "diagnostics") -> ReconciliationResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    report = out / f"broker_fill_reconciliation_{stamp}.csv"
    summary = out / f"broker_fill_reconciliation_summary_{stamp}.md"
    result.frame.to_csv(report, index=False)
    summary.write_text("# Broker Fill Reconciliation\n\n" + "\n".join(f"- {k}: {v}" for k, v in result.summary.items()) + "\n", encoding="utf-8")
    return ReconciliationResult(result.frame, result.summary, report, summary)
