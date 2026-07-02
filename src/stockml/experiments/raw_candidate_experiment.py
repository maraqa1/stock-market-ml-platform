from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import latest_file
from stockml.safety.live_disabled import assert_live_disabled
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.order_builder import extended_limit_price, validate_order_payload
from stockml.experiments.raw_candidate_experiment_policy import (
    EXPERIMENT_MODE,
    RawCandidateExperimentConfig,
    candidate_policy_decision,
    events_ledger_path,
    experiment_dir,
    load_raw_candidate_experiment_config,
    policy_can_start,
    project_root,
    read_ledger,
    trades_ledger_path,
)


EVENT_COLUMNS = [
    "experiment_id",
    "event_at",
    "symbol",
    "side",
    "qty",
    "notional",
    "entry_price",
    "order_type",
    "limit_price",
    "status",
    "original_raw_rank",
    "execution_rank_if_any",
    "original_status",
    "original_block_reasons",
    "trade_action",
    "directional_action",
    "no_decision_experiment",
    "would_have_passed_normal_gates",
    "normal_gate_failures",
    "experiment_reason",
    "current_price",
    "unrealized_pnl",
    "realized_pnl",
    "holding_minutes",
    "experiment_mode",
    "strategy_mode",
    "candidate_source_original",
    "client_order_id",
    "broker_order_id",
]


@dataclass(frozen=True)
class RawExperimentResult:
    status: str
    selected: int
    submitted: int
    skipped: int
    events_path: Path
    trades_path: Path
    candidates: pd.DataFrame


def _clean(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "") or pd.isna(value):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _int_qty(notional: float, price: float) -> int:
    if notional <= 0 or price <= 0:
        return 0
    return max(1, int(notional // price))


def latest_candidate_pool(root: Path | None = None) -> Path | None:
    base = project_root(root)
    return latest_file(base / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")


def load_candidates(path: Path | None = None, *, root: Path | None = None) -> pd.DataFrame:
    candidate_path = path or latest_candidate_pool(root)
    if candidate_path is None or not candidate_path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(candidate_path, low_memory=False)
    except Exception:
        return pd.DataFrame()
    if "symbol" not in frame.columns and "ticker" in frame.columns:
        frame["symbol"] = frame["ticker"]
    if "symbol" in frame.columns:
        frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame["candidate_source_original"] = str(candidate_path)
    return frame


def _normal_status(row: pd.Series) -> tuple[str, str]:
    status = _clean(row.get("status")) or _clean(row.get("trade_quality_status"))
    if not status:
        executable = str(row.get("executable", "")).strip().lower() in {"true", "1", "yes"}
        status = "executable" if executable else "unknown"
    reasons = _clean(row.get("all_block_reasons")) or _clean(row.get("primary_block_reason")) or _clean(row.get("trade_quality_reason"))
    return status.lower(), reasons


def _candidate_side(row: pd.Series) -> tuple[str, str, bool]:
    action = _clean(row.get("trade_action") or row.get("source_trade_action"))
    directional = _clean(row.get("directional_action") or row.get("directional_signal"))
    action_lower = action.lower().replace("_", " ")
    source = action
    no_decision = action_lower in {"no decision", "neutral", "no decision row", ""}
    if no_decision and directional:
        source = directional
    text = source.lower()
    if text in {"long", "buy", "bullish"}:
        return "buy", source, no_decision
    if text in {"short", "sell", "bearish"}:
        return "sell", source, no_decision
    return "", source, no_decision


def prepare_experiment_candidates(
    candidates: pd.DataFrame,
    config: RawCandidateExperimentConfig,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    rows: list[dict[str, Any]] = []
    for idx, row in candidates.iterrows():
        symbol = _clean(row.get("symbol") or row.get("ticker")).upper()
        side, directional_action, no_decision = _candidate_side(row)
        if not symbol or not side:
            continue
        original_status, reasons = _normal_status(row)
        price = _float(row.get("current_price"), 0.0) or _float(row.get("close"), 0.0) or _float(row.get("limit_price"), 0.0)
        notional = min(config.max_notional_per_trade, max(0.0, _float(row.get("approved_notional"), config.max_notional_per_trade)))
        qty = _int_qty(notional, price)
        raw_rank = _float(row.get("raw_rank"), 0.0) or _float(row.get("candidate_rank"), 0.0) or float(idx + 1)
        would_pass = original_status in {"approved", "executable", "reduced"}
        rows.append(
            {
                "symbol": symbol,
                "experiment_side": side,
                "qty": qty,
                "notional": round(notional, 2),
                "entry_price": price,
                "current_price": price,
                "original_raw_rank": int(raw_rank) if raw_rank else "",
                "execution_rank_if_any": row.get("execution_rank", ""),
                "original_status": original_status,
                "original_block_reasons": reasons,
                "trade_action": row.get("trade_action", ""),
                "original_trade_action": row.get("trade_action", row.get("source_trade_action", "")),
                "directional_action": directional_action,
                "no_decision_experiment": bool(no_decision),
                "would_have_passed_normal_gates": bool(would_pass),
                "normal_gate_failures": "" if would_pass else reasons,
                "candidate_source_original": row.get("candidate_source_original", ""),
                "experiment_mode": EXPERIMENT_MODE,
                "strategy_mode": "experiment",
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["would_have_passed_normal_gates", "original_raw_rank", "symbol"], ascending=[True, True, True]).reset_index(drop=True)


def _append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    if path.exists() and path.stat().st_size > 0:
        existing = read_ledger(path)
        frame = pd.concat([existing, frame], ignore_index=True)
    frame.to_csv(path, index=False)


def _open_symbols_from_latest_positions(root: Path | None = None) -> set[str]:
    base = project_root(root)
    path = latest_file(base / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv")
    if path is None or not path.exists() or path.stat().st_size == 0:
        return set()
    try:
        frame = pd.read_csv(path, usecols=lambda col: col in {"symbol"}, low_memory=False)
    except Exception:
        return set()
    if "symbol" not in frame.columns:
        return set()
    return set(frame["symbol"].dropna().astype(str).str.upper().str.strip())


def _open_experiment_symbols(root: Path | None = None) -> set[str]:
    trades = read_ledger(trades_ledger_path(root))
    if trades.empty or "symbol" not in trades.columns:
        return set()
    if "status" in trades.columns:
        trades = trades[~trades["status"].astype(str).str.lower().isin({"closed", "canceled", "rejected"})]
    return set(trades["symbol"].dropna().astype(str).str.upper().str.strip())


def _order_payload(row: pd.Series, *, now: datetime, extended_hours: bool = False) -> dict[str, Any]:
    side = _clean(row.get("experiment_side"))
    symbol = _clean(row.get("symbol")).upper()
    limit_price = round(_float(row.get("current_price"), 0.0), 2)
    if limit_price <= 0:
        limit_price = round(_float(row.get("entry_price"), 0.0), 2)
    return {
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "qty": str(int(row.get("qty") or 0)),
        "limit_price": limit_price,
        "extended_hours": bool(extended_hours),
        "client_order_id": f"stockml-rawexp-{now:%Y%m%d%H%M%S}-{symbol}-{side}"[:48],
    }


def _event_row(row: pd.Series, *, now: datetime, status: str, reason: str, order: dict[str, Any] | None = None, broker_order_id: str = "") -> dict[str, Any]:
    order = order or {}
    return {
        "experiment_id": f"rawexp-{now:%Y%m%d}",
        "event_at": now.isoformat(),
        "symbol": row.get("symbol", ""),
        "side": row.get("experiment_side", ""),
        "qty": row.get("qty", 0),
        "notional": row.get("notional", 0),
        "entry_price": row.get("entry_price", ""),
        "order_type": order.get("type", "limit"),
        "limit_price": order.get("limit_price", ""),
        "status": status,
        "original_raw_rank": row.get("original_raw_rank", ""),
        "execution_rank_if_any": row.get("execution_rank_if_any", ""),
        "original_status": row.get("original_status", ""),
        "original_block_reasons": row.get("original_block_reasons", ""),
        "trade_action": row.get("trade_action", ""),
        "directional_action": row.get("directional_action", ""),
        "no_decision_experiment": row.get("no_decision_experiment", False),
        "would_have_passed_normal_gates": row.get("would_have_passed_normal_gates", False),
        "normal_gate_failures": row.get("normal_gate_failures", ""),
        "experiment_reason": reason,
        "current_price": row.get("current_price", ""),
        "unrealized_pnl": "",
        "realized_pnl": "",
        "holding_minutes": "",
        "experiment_mode": EXPERIMENT_MODE,
        "strategy_mode": "experiment",
        "candidate_source_original": row.get("candidate_source_original", ""),
        "client_order_id": order.get("client_order_id", ""),
        "broker_order_id": broker_order_id,
    }


def run_raw_candidate_experiment(
    *,
    root: Path | None = None,
    candidate_file: Path | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
    config: RawCandidateExperimentConfig | None = None,
    client: AlpacaPaperClient | None = None,
) -> RawExperimentResult:
    assert_live_disabled()
    root = project_root(root)
    now = now or datetime.now(timezone.utc)
    config = config or load_raw_candidate_experiment_config(root)
    trade_config = alpaca_config()
    start = policy_can_start(config, root=root, run_date=now.date(), trade_config=trade_config, dry_run=dry_run)
    events_path = events_ledger_path(root, now.date())
    trades_path = trades_ledger_path(root, now.date())
    if not start.allowed:
        event = _event_row(pd.Series({"symbol": "", "experiment_side": ""}), now=now, status="blocked", reason=start.reason)
        _append_csv(events_path, [event])
        return RawExperimentResult("blocked", 0, 0, 1, events_path, trades_path, pd.DataFrame())

    candidates = prepare_experiment_candidates(load_candidates(candidate_file, root=root), config)
    open_experiment = _open_experiment_symbols(root)
    open_normal = _open_symbols_from_latest_positions(root)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    submitted = 0
    client = client or AlpacaPaperClient(trade_config)

    for _, row in candidates.iterrows():
        decision = candidate_policy_decision(
            row,
            config,
            root=root,
            run_date=now.date(),
            cycle_selected=len(selected),
            open_experiment_symbols=open_experiment,
            open_normal_symbols=open_normal,
        )
        if not decision.allowed:
            skipped.append(_event_row(row, now=now, status="skipped", reason=decision.reason))
            continue
        order = _order_payload(row, now=now, extended_hours=trade_config.extended_hours or trade_config.overnight_trading_enabled)
        valid = validate_order_payload(order, max_order_notional=config.max_notional_per_trade)
        if not valid.valid:
            skipped.append(_event_row(row, now=now, status="skipped", reason=valid.reason, order=order))
            continue
        if dry_run:
            selected.append(_event_row(row, now=now, status="dry_run_selected", reason=decision.reason, order=order))
            continue
        if not trade_config.submit_orders:
            skipped.append(_event_row(row, now=now, status="skipped", reason="submit_orders_disabled", order=order))
            continue
        response = client.submit_order(order)
        broker_order_id = str(response.get("id") or "")
        submitted += 1
        selected.append(_event_row(row, now=now, status="submitted", reason=decision.reason, order=order, broker_order_id=broker_order_id))

    _append_csv(events_path, [*selected, *skipped])
    if selected:
        _append_csv(trades_path, selected)
    status = "dry_run" if dry_run else "ok"
    return RawExperimentResult(status, len(selected), submitted, len(skipped), events_path, trades_path, candidates)
