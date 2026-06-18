from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import desc, select

from stockml.agents.position_decision_engine import build_position_decisions
from stockml.autopilot.basket_risk import evaluate_basket_risk, load_basket_risk_config
from stockml.autopilot.eod import run_eod_tick
from stockml.autopilot.open import (
    apply_auto_open,
    latest_flat_account_fallback_candidates,
    latest_near_miss_fallback_candidates,
    latest_plan_fallback_candidates,
    latest_per_symbol_forecast_fallback_candidates,
    latest_holding_review_map,
    latest_strong_candidates,
    load_auto_open_config,
    ranked_fallback_candidates,
)
from stockml.autopilot.rotate import apply_auto_rotations
from stockml.autopilot.position_health import PositionHealthRules, classify_position_health
from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import intraday_decisions
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.anti_churn_guard import enrich_open_positions_with_order_history, guard_actions, load_recent_trade_history, write_anti_churn_report
from stockml.trading.holding_period import generate_holding_period_report
from stockml.trading.manual_position_actions import apply_manual_position_action
from stockml.trading.paper_trader import refresh_order_tracking
from stockml.trading.profit_taking import ProfitTakingRules, classify_profit_taking
from stockml.trading.stale_entry_orders import cancel_stale_entry_orders


STATE_VERSION = 1
TERMINAL_ORDER_STATES = {"filled", "canceled", "cancelled", "expired", "rejected"}
OPEN_ORDER_STATES = {"accepted", "new", "pending_new", "pending_replace", "submitted", "partially_filled", "partial"}
DEFENSIVE_STALE_LOSS_THRESHOLD = -0.025
DEFENSIVE_UNKNOWN_LOSS_THRESHOLD = -0.02
HARD_STOP_LOSS_THRESHOLD = -0.04
DEFAULT_TRAILING_PROFIT_ARM_PCT = 2.0
DEFAULT_TRAILING_GIVEBACK_PCT = 1.0
AUTOPILOT_MODE_ORDER = ["observe", "paper_assist", "paper_autopilot", "ai_gated_paper"]
AUTOPILOT_MODES: dict[str, dict[str, Any]] = {
    "observe": {
        "label": "Observe",
        "button_label": "Switch to Observe",
        "summary": "Track broker state, monitor decisions, intraday decisions, and candidate evaluations. No automatic broker action.",
        "execution_policy": "read_only",
    },
    "paper_assist": {
        "label": "Paper Assist",
        "button_label": "Switch to Paper Assist",
        "summary": "Prepare paper actions for operator review. The operator still applies or overrides every action.",
        "execution_policy": "operator_confirmed",
    },
    "paper_autopilot": {
        "label": "Paper Autopilot",
        "button_label": "Switch to Paper Autopilot",
        "summary": "Allow the paper state machine to execute approved paper-cycle actions once their guards are implemented.",
        "execution_policy": "paper_guarded",
    },
    "ai_gated_paper": {
        "label": "AI-Gated Paper",
        "button_label": "Switch to AI-Gated Paper",
        "summary": "Require an AI reviewer to approve paper-cycle actions before execution. No live trading path exists.",
        "execution_policy": "ai_reviewed_paper",
    },
}
TICK_LOG_COLUMNS = [
    "timestamp",
    "mode",
    "status",
    "phase",
    "open_orders",
    "broker_open_orders",
    "tracked_open_orders",
    "open_positions",
    "orders_tracked",
    "termination_reason",
    "last_error",
    "tracking_path",
    "positions_path",
    "intraday_allows",
    "intraday_blocks",
    "monitor_actions",
    "monitor_close",
    "monitor_rotate",
    "monitor_watch",
    "autopilot_actions",
    "autopilot_close_submitted",
    "autopilot_defensive_close_submitted",
    "autopilot_hard_stop_submitted",
    "autopilot_health_close_submitted",
    "autopilot_trailing_close_submitted",
    "autopilot_replace_close_submitted",
    "autopilot_action_notes",
    "autopilot_open_attempted",
    "autopilot_open_submitted",
    "autopilot_open_blocked",
    "autopilot_open_notes",
    "stale_entry_open_orders",
    "stale_entry_candidates",
    "stale_entry_canceled",
    "stale_entry_notes",
    "stale_entry_path",
    "eod_state",
    "eod_actions",
    "eod_flatten_submitted",
    "eod_remaining",
    "eod_banner",
    "eod_action_notes",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(root: Path | None = None) -> Path:
    if root is None:
        return PORTAL_OUTPUTS_DIR / "paper_autopilot_state.json"
    return Path(root) / "data" / "portal_outputs" / "paper_autopilot_state.json"


def _autopilot_dir(root: Path | None = None) -> Path:
    if root is None:
        return PORTAL_OUTPUTS_DIR.parent / "trading" / "autopilot"
    return Path(root) / "data" / "trading" / "autopilot"


def _tick_log_path(root: Path | None = None, now: datetime | None = None) -> Path:
    day = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return _autopilot_dir(root) / f"autopilot_ticks_{day}.csv"


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "name": "Paper Autopilot",
        "mode": "observe",
        "status": "idle",
        "phase": "idle",
        "started_at": "",
        "updated_at": "",
        "stopped_at": "",
        "termination_reason": "",
        "last_tick_at": "",
        "last_error": "",
        "open_orders": 0,
        "broker_open_orders": 0,
        "tracked_open_orders": 0,
        "open_positions": 0,
        "orders_tracked": 0,
        "tracking_path": "",
        "positions_path": "",
        "intraday_allows": 0,
        "intraday_blocks": 0,
        "latest_intraday_at": "",
        "monitor_actions": 0,
        "monitor_close": 0,
        "monitor_rotate": 0,
        "monitor_watch": 0,
        "latest_monitor_at": "",
        "autopilot_actions": 0,
        "autopilot_close_submitted": 0,
        "autopilot_defensive_close_submitted": 0,
        "autopilot_hard_stop_submitted": 0,
        "autopilot_health_close_submitted": 0,
        "autopilot_trailing_close_submitted": 0,
        "autopilot_replace_close_submitted": 0,
        "autopilot_action_notes": "",
        "autopilot_open_attempted": 0,
        "autopilot_open_submitted": 0,
        "autopilot_open_blocked": 0,
        "autopilot_open_notes": "",
        "stale_entry_open_orders": 0,
        "stale_entry_candidates": 0,
        "stale_entry_canceled": 0,
        "stale_entry_notes": "",
        "stale_entry_path": "",
        "position_peak_plpc": {},
        "eod_state": "inactive",
        "eod_actions": 0,
        "eod_flatten_submitted": 0,
        "eod_remaining": 0,
        "eod_banner": "",
        "eod_action_notes": "",
        "paper_only": True,
        "live_trading_enabled": False,
    }


def load_state(root: Path | None = None) -> dict[str, Any]:
    state = _default_state()
    path = _state_path(root)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                state.update(stored)
        except Exception:
            state["last_error"] = "state_file_unreadable"
    return state


def save_state(state: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    out = _default_state()
    out.update(state)
    if out.get("mode") not in AUTOPILOT_MODES:
        out["mode"] = "observe"
    out["version"] = STATE_VERSION
    out["paper_only"] = True
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def append_tick_log(state: dict[str, Any], root: Path | None = None) -> Path:
    path = _tick_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {column: state.get(column, "") for column in TICK_LOG_COLUMNS}
    row["timestamp"] = state.get("last_tick_at") or state.get("updated_at") or _now()
    row["mode"] = state.get("mode") or "observe"
    frame = pd.DataFrame([row], columns=TICK_LOG_COLUMNS)
    frame.to_csv(path, mode="a", index=False, header=not path.exists())
    return path


def recent_tick_logs(root: Path | None = None, limit: int = 10) -> list[dict[str, Any]]:
    directory = _autopilot_dir(root)
    if not directory.exists():
        return []
    files = sorted(directory.glob("autopilot_ticks_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    frames = []
    for path in files[:3]:
        try:
            frames.append(pd.read_csv(path, low_memory=False))
        except Exception:
            pass
    if not frames:
        return []
    frame = pd.concat(frames, ignore_index=True)
    return frame.tail(max(1, limit)).iloc[::-1].fillna("").to_dict("records")


def mode_options() -> list[dict[str, str]]:
    return [{"value": key, **AUTOPILOT_MODES[key]} for key in AUTOPILOT_MODE_ORDER]


def capability_rows() -> list[dict[str, Any]]:
    return [
        {
            "mode": "observe",
            "label": "Observe",
            "tracks": True,
            "operator_review": True,
            "auto_close": False,
            "auto_open": False,
            "auto_rotate": False,
            "description": "Read-only tracking, logging, monitor and candidate visibility.",
        },
        {
            "mode": "paper_assist",
            "label": "Paper Assist",
            "tracks": True,
            "operator_review": True,
            "auto_close": False,
            "auto_open": False,
            "auto_rotate": False,
            "description": "Prepares actions for manual operator confirmation.",
        },
        {
            "mode": "paper_autopilot",
            "label": "Paper Autopilot",
            "tracks": True,
            "operator_review": False,
            "auto_close": True,
            "auto_open": True,
            "auto_rotate": True,
            "description": "Can submit guarded paper close orders, replacement closes, and config-gated paper opens.",
        },
        {
            "mode": "ai_gated_paper",
            "label": "AI-Gated Paper",
            "tracks": True,
            "operator_review": True,
            "auto_close": False,
            "auto_open": False,
            "auto_rotate": False,
            "description": "Reserved for future AI-reviewed paper actions.",
        },
    ]


def rule_rows() -> list[dict[str, Any]]:
    profit_rules = ProfitTakingRules.from_percentages(DEFAULT_TRAILING_PROFIT_ARM_PCT, DEFAULT_TRAILING_GIVEBACK_PCT)
    return [
        {
            "rule": "Monitor close",
            "trigger": "Latest monitor decision is close.",
            "action": "Submit paper close order",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Hard stop-loss",
            "trigger": f"Unrealized return <= {HARD_STOP_LOSS_THRESHOLD:.1%}.",
            "action": "Submit paper close order",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Defensive stale loser",
            "trigger": (
                f"Signal stale and unrealized return <= {DEFENSIVE_STALE_LOSS_THRESHOLD:.1%}, "
                f"or signal unknown and unrealized return <= {DEFENSIVE_UNKNOWN_LOSS_THRESHOLD:.1%}."
            ),
            "action": "Submit paper close order",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Trailing profit protection",
            "trigger": (
                f"Stale/unknown signal: peak return >= {profit_rules.trailing_profit_min:.1%} "
                f"and giveback >= {profit_rules.trailing_giveback_threshold:.1%}. "
                f"Fresh signal: larger giveback required."
            ),
            "action": "Submit paper close order",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Open candidate",
            "trigger": "Intraday promotion is strong and the basket has an empty slot.",
            "action": "Submit guarded paper open order when enabled",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Rotation",
            "trigger": "A better candidate exists than a current holding.",
            "action": "Close current paper position when rotate is enabled; replacement open waits for guarded auto-open",
            "active_in": "Paper Autopilot",
        },
    ]


def set_mode(mode: str, root: Path | None = None) -> dict[str, Any]:
    clean = str(mode or "").strip().lower()
    state = load_state(root)
    stamp = _now()
    if clean not in AUTOPILOT_MODES:
        state.update({"last_error": f"unsupported_autopilot_mode:{clean}", "updated_at": stamp})
        return save_state(state, root)
    state.update({"mode": clean, "updated_at": stamp, "last_error": ""})
    return save_state(state, root)


def start(root: Path | None = None) -> dict[str, Any]:
    cfg = alpaca_config()
    state = load_state(root)
    stamp = _now()
    state.update(
        {
            "status": "running",
            "phase": "tracking_orders",
            "started_at": state.get("started_at") or stamp,
            "updated_at": stamp,
            "stopped_at": "",
            "termination_reason": "",
            "last_error": "",
            "paper_only": True,
            "live_trading_enabled": bool(cfg.live_trading_enabled),
        }
    )
    if cfg.live_trading_enabled:
        state.update(
            {
                "status": "stopped",
                "phase": "guardrail_stop",
                "stopped_at": stamp,
                "termination_reason": "live_trading_enabled_guardrail",
                "last_error": "Live trading is enabled in config; Paper Autopilot refused to start.",
            }
        )
    return save_state(state, root)


def pause(root: Path | None = None) -> dict[str, Any]:
    state = load_state(root)
    if state.get("status") == "running":
        state.update({"status": "paused", "phase": "paused", "updated_at": _now()})
    return save_state(state, root)


def resume(root: Path | None = None) -> dict[str, Any]:
    state = load_state(root)
    if state.get("status") == "paused":
        state.update({"status": "running", "phase": "tracking_orders", "updated_at": _now(), "last_error": ""})
    return save_state(state, root)


def stop(root: Path | None = None, reason: str = "stopped_by_operator") -> dict[str, Any]:
    state = load_state(root)
    stamp = _now()
    state.update({"status": "stopped", "phase": "stopped", "updated_at": stamp, "stopped_at": stamp, "termination_reason": reason})
    return save_state(state, root)


def _read_csv(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _count_open_orders(tracking: pd.DataFrame) -> int:
    if tracking.empty:
        return 0
    status = tracking.get("alpaca_status", tracking.get("status", pd.Series("", index=tracking.index))).fillna("").astype(str).str.lower()
    fallback = tracking.get("status", pd.Series("", index=tracking.index)).fillna("").astype(str).str.lower()
    effective = status.where(status.ne(""), fallback)
    return int(effective.isin(OPEN_ORDER_STATES).sum())


def _count_broker_open_orders(cfg: Any) -> int:
    if not cfg.api_key or not cfg.secret_key:
        return 0
    orders = AlpacaPaperClient(cfg).list_orders(status="open")
    return len(orders)


def _agent_decisions_dir(root: Path | None = None) -> Path:
    if root is None:
        return PORTAL_OUTPUTS_DIR.parent / "trading" / "agent_decisions"
    return Path(root) / "data" / "trading" / "agent_decisions"


def _portal_outputs_dir(root: Path | None = None) -> Path:
    if root is None:
        return PORTAL_OUTPUTS_DIR
    return Path(root) / "data" / "portal_outputs"


def _latest_csv(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_monitor_decision_summary(root: Path | None = None) -> dict[str, Any]:
    path = _latest_csv(_agent_decisions_dir(root), "position_decisions_*.csv")
    frame = _read_csv(path)
    if frame.empty or "decision" not in frame.columns:
        return {"monitor_actions": 0, "monitor_close": 0, "monitor_rotate": 0, "monitor_watch": 0, "latest_monitor_at": ""}
    decisions = frame["decision"].fillna("").astype(str).str.lower()
    actions = decisions.isin(["watch", "close", "rotate"])
    return {
        "monitor_actions": int(actions.sum()),
        "monitor_close": int((decisions == "close").sum()),
        "monitor_rotate": int((decisions == "rotate").sum()),
        "monitor_watch": int((decisions == "watch").sum()),
        "latest_monitor_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds") if path else "",
    }


def load_monitor_decisions(root: Path | None = None) -> pd.DataFrame:
    path = _latest_csv(_agent_decisions_dir(root), "position_decisions_*.csv")
    return _read_csv(path)


def build_fresh_monitor_decisions(
    root: Path | None,
    positions: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, Path | None]:
    if positions.empty:
        return pd.DataFrame(), None

    portal_dir = _portal_outputs_dir(root)
    plan_path = _latest_csv(portal_dir, "08_alpaca_paper_order_plan_*.csv")
    result_path = _latest_csv(portal_dir, "08_alpaca_paper_order_results_*.csv")
    candidate_pool_path = _latest_csv(portal_dir, "08_alpaca_paper_candidate_pool_*.csv")
    holding_review_path = _latest_csv(
        (Path(root) / "data" / "trading" / "holding_period") if root else (PORTAL_OUTPUTS_DIR.parent / "trading" / "holding_period"),
        "holding_review_*.csv",
    )
    plan = _read_csv(plan_path)
    results = _read_csv(result_path)
    candidate_pool = _read_csv(candidate_pool_path)
    holding_review = _read_csv(holding_review_path)
    fallback_signal_time = (
        datetime.fromtimestamp(plan_path.stat().st_mtime, tz=timezone.utc)
        if plan_path and plan_path.exists()
        else None
    )
    decisions = build_position_decisions(
        positions,
        plan,
        results,
        candidate_pool,
        holding_review,
        now=now or datetime.now(timezone.utc),
        signal_ttl_minutes=10,
        fallback_signal_time=fallback_signal_time,
    )
    directory = _agent_decisions_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"position_decisions_{timestamp()}.csv"
    decisions.to_csv(path, index=False)
    return decisions, path


def attach_holding_review_to_positions(positions: pd.DataFrame, root: Path | None = None) -> pd.DataFrame:
    if positions.empty or "symbol" not in positions.columns:
        return positions
    reviews = latest_holding_review_map(root)
    if not reviews:
        return positions
    out = positions.copy()
    for column in [
        "trading_stream",
        "recommended_holding_days",
        "review_after_days",
        "max_holding_days",
        "holding_quality",
        "holding_gate_reason",
    ]:
        if column not in out.columns:
            out[column] = ""
    for idx, row in out.iterrows():
        review = reviews.get(str(row.get("symbol") or "").upper().strip())
        if not review:
            continue
        for column in [
            "trading_stream",
            "recommended_holding_days",
            "review_after_days",
            "max_holding_days",
            "holding_quality",
            "holding_gate_reason",
        ]:
            out.at[idx, column] = review.get(column, "")
    return out


def _position_plpc_by_symbol(positions: pd.DataFrame) -> dict[str, float]:
    if positions.empty or "symbol" not in positions.columns:
        return {}
    out: dict[str, float] = {}
    for row in positions.fillna("").to_dict("records"):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        try:
            out[symbol] = float(row.get("unrealized_plpc") or 0)
        except Exception:
            out[symbol] = 0.0
    return out


def update_position_peaks(state: dict[str, Any], positions: pd.DataFrame) -> dict[str, float]:
    current = _position_plpc_by_symbol(positions)
    existing = state.get("position_peak_plpc") if isinstance(state.get("position_peak_plpc"), dict) else {}
    peaks: dict[str, float] = {}
    for symbol, plpc in current.items():
        try:
            previous = float((existing or {}).get(symbol, plpc))
        except Exception:
            previous = plpc
        peaks[symbol] = max(previous, plpc)
    state["position_peak_plpc"] = peaks
    return peaks


def _auto_close_candidates(root: Path | None, positions: pd.DataFrame, state: dict[str, Any] | None = None) -> pd.DataFrame:
    path = _latest_csv(_agent_decisions_dir(root), "position_decisions_*.csv")
    decisions = _read_csv(path)
    if decisions.empty or positions.empty or "decision" not in decisions.columns or "symbol" not in decisions.columns:
        return pd.DataFrame()
    open_symbols = {str(symbol).upper() for symbol in positions.get("symbol", pd.Series(dtype=str)).dropna()}
    if not open_symbols:
        return pd.DataFrame()
    frame = decisions.copy()
    frame["__symbol"] = frame["symbol"].fillna("").astype(str).str.upper()
    frame["__decision"] = frame["decision"].fillna("").astype(str).str.lower()
    frame["__reason"] = frame.get("decision_reason", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__plpc"] = pd.to_numeric(frame.get("unrealized_plpc", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
    peaks = (state or {}).get("position_peak_plpc") if isinstance((state or {}).get("position_peak_plpc"), dict) else {}
    frame["__peak_plpc"] = frame["__symbol"].map(lambda symbol: float((peaks or {}).get(symbol, frame.loc[frame["__symbol"] == symbol, "__plpc"].iloc[-1])))
    auto_config = load_auto_open_config(root=root)
    health_rules = PositionHealthRules(
        max_position_loss_pct=float(auto_config.max_position_loss_pct),
        hard_stop_loss_pct=abs(HARD_STOP_LOSS_THRESHOLD) * 100.0,
    )
    health_rows = [
        classify_position_health(
            {
                **row.to_dict(),
                "unrealized_plpc": row.get("__plpc"),
                "decision_reason": row.get("__reason"),
            },
            health_rules,
        )
        for _, row in frame.iterrows()
    ]
    frame["__health_status"] = [str(value.get("position_health_status") or "") for value in health_rows]
    frame["__health_reason"] = [str(value.get("position_health_reason") or "") for value in health_rows]
    close_automation_mode = str(auto_config.close_automation_mode or "automatic").lower()
    profit_rules = ProfitTakingRules.from_percentages(
        auto_config.trailing_profit_arm_pct,
        auto_config.trailing_profit_giveback_pct,
    )
    profit_rows = [
        classify_profit_taking(
            current_plpc=row.get("__plpc"),
            peak_plpc=row.get("__peak_plpc"),
            decision_reason=row.get("__reason"),
            decision=row.get("__decision"),
            rules=profit_rules,
        )
        for _, row in frame.iterrows()
    ]
    frame["__profit_close_triggered"] = [bool(value.get("close_triggered")) for value in profit_rows]
    frame["__profit_close_reason"] = [str(value.get("close_trigger_reason") or "") for value in profit_rows]
    health_close_enabled = close_automation_mode != "review_only"
    health_close_now = frame["__health_status"] == "close_now"
    health_close_candidate = frame["__health_status"] == "close_candidate"
    health_close = health_close_enabled & (health_close_now | health_close_candidate)
    explicit_close = frame["__decision"] == "close"
    replace_close = frame["__decision"].isin(["replace", "rotate"])
    hard_stop = frame["__plpc"] <= HARD_STOP_LOSS_THRESHOLD
    unknown_signal_loss = frame["__reason"].str.contains("latest_signal_unknown", na=False) & (
        frame["__plpc"] <= DEFENSIVE_UNKNOWN_LOSS_THRESHOLD
    )
    defensive_stale_loss = (
        (frame["__decision"] == "watch")
        & (
            (frame["__reason"].str.contains("signal_stale", na=False) & (frame["__plpc"] <= DEFENSIVE_STALE_LOSS_THRESHOLD))
            | unknown_signal_loss
        )
    )
    trailing_profit = frame["__profit_close_triggered"]
    rotate_enabled = auto_config.rotate_enabled
    out = frame[
        (explicit_close | health_close | hard_stop | defensive_stale_loss | trailing_profit | (replace_close & rotate_enabled))
        & frame["__symbol"].isin(open_symbols)
    ].copy()
    out["__autopilot_close_reason"] = "monitor_close"
    out.loc[health_close.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "position_health_close_candidate"
    out.loc[(health_close_now & health_close_enabled).reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "position_health_close_now"
    out.loc[(replace_close & rotate_enabled).reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "monitor_replace"
    out.loc[hard_stop.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "hard_stop_loss"
    out.loc[defensive_stale_loss.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "defensive_stale_loss"
    out.loc[trailing_profit.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = out.loc[
        trailing_profit.reindex(out.index, fill_value=False), "__profit_close_reason"
    ].replace("", "trailing_profit_giveback")
    out.loc[explicit_close.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "monitor_close"
    return out


def apply_paper_autopilot_decisions(
    root: Path | None,
    positions: pd.DataFrame,
    state: dict[str, Any] | None = None,
    *,
    action_func: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply the first narrow Paper Autopilot authority slice.

    Paper Autopilot may submit paper close orders for monitor close decisions,
    monitor replace recommendations, hard stop-loss breaches, stale losing
    positions, and stale winners that give back too much profit. Replacement
    opens remain delegated to the guarded auto-open path.
    """
    candidates = _auto_close_candidates(root, positions, state)
    anti_churn_blocked = 0
    anti_churn_path = ""
    if not candidates.empty:
        position_rows = positions.to_dict("records") if isinstance(positions, pd.DataFrame) else list(positions or [])
        enriched_positions = enrich_open_positions_with_order_history(position_rows, root=root)
        actions = [
            {
                "symbol": row.get("__symbol") or row.get("symbol"),
                "action": "close",
                "side": "sell",
                "reason": row.get("__autopilot_close_reason") or row.get("decision_reason"),
            }
            for row in candidates.to_dict("records")
        ]
        allowed_actions, anti_churn_report = guard_actions(
            actions,
            open_positions=enriched_positions,
            trade_history=load_recent_trade_history(root=root),
            now=datetime.now(timezone.utc),
            cycle_id=f"paper_autopilot_close_{timestamp()}",
        )
        if not anti_churn_report.empty:
            anti_churn_blocked = len(anti_churn_report)
            anti_churn_path = str(write_anti_churn_report(anti_churn_report, root=root))
            allowed_symbols = {str(row.get("symbol") or "").upper() for row in allowed_actions}
            candidates = candidates[candidates["__symbol"].astype(str).str.upper().isin(allowed_symbols)].copy()
    if candidates.empty:
        return {
            "autopilot_actions": 0,
            "autopilot_close_submitted": 0,
            "autopilot_defensive_close_submitted": 0,
            "autopilot_hard_stop_submitted": 0,
            "autopilot_health_close_submitted": 0,
            "autopilot_trailing_close_submitted": 0,
            "autopilot_replace_close_submitted": 0,
            "autopilot_action_notes": "",
            "autopilot_open_attempted": 0,
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "",
            "autopilot_anti_churn_blocked": anti_churn_blocked,
            "autopilot_anti_churn_report_path": anti_churn_path,
        }

    apply_action = action_func or (lambda symbol, action: apply_manual_position_action(symbol, action))
    notes: list[str] = []
    submitted = 0
    dry_run = 0
    rejected = 0
    errors = 0
    skipped = 0
    defensive_submitted = 0
    hard_stop_submitted = 0
    health_submitted = 0
    trailing_submitted = 0
    replace_submitted = 0
    actions = 0
    seen: set[str] = set()
    for row in candidates.fillna("").to_dict("records"):
        symbol = str(row.get("__symbol") or row.get("symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result = apply_action(symbol, "close")
        actions += 1
        status = str(result.get("status") or "")
        message = str(result.get("message") or "")
        if status == "submitted":
            submitted += 1
            reason = str(row.get("__autopilot_close_reason") or "")
            if reason == "defensive_stale_loss":
                defensive_submitted += 1
            elif reason == "hard_stop_loss":
                hard_stop_submitted += 1
            elif reason in {"position_health_close_candidate", "position_health_close_now"}:
                health_submitted += 1
            elif reason in {"trailing_profit_giveback", "fresh_signal_profit_giveback"}:
                trailing_submitted += 1
            elif reason == "monitor_replace":
                replace_submitted += 1
        elif status == "dry_run":
            dry_run += 1
        elif status == "error":
            errors += 1
        elif status == "skipped":
            skipped += 1
        else:
            rejected += 1
        close_reason = str(row.get("__autopilot_close_reason") or "monitor_close")
        notes.append(f"{symbol}:{close_reason}:{status or 'unknown'}:{message or 'no_message'}")
    return {
        "autopilot_actions": actions,
        "autopilot_close_submitted": submitted,
        "autopilot_close_dry_run": dry_run,
        "autopilot_close_rejected": rejected,
        "autopilot_close_error": errors,
        "autopilot_close_skipped_existing": skipped,
        "autopilot_defensive_close_submitted": defensive_submitted,
        "autopilot_hard_stop_submitted": hard_stop_submitted,
        "autopilot_health_close_submitted": health_submitted,
        "autopilot_trailing_close_submitted": trailing_submitted,
        "autopilot_replace_close_submitted": replace_submitted,
        "autopilot_action_notes": "; ".join(notes[:10]),
        "autopilot_anti_churn_blocked": anti_churn_blocked,
        "autopilot_anti_churn_report_path": anti_churn_path,
    }


def load_intraday_decision_summary(limit: int = 500) -> dict[str, Any]:
    engine = get_engine(required=False)
    if engine is None:
        return {"intraday_allows": 0, "intraday_blocks": 0, "latest_intraday_at": ""}
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(intraday_decisions.c.decided_at, intraday_decisions.c.verdict)
                .order_by(desc(intraday_decisions.c.decided_at))
                .limit(limit)
            ).all()
    except Exception:
        return {"intraday_allows": 0, "intraday_blocks": 0, "latest_intraday_at": ""}
    verdicts = [str(row.verdict or "").lower() for row in rows]
    latest = rows[0].decided_at.isoformat(timespec="seconds") if rows and rows[0].decided_at else ""
    return {
        "intraday_allows": int(sum(1 for verdict in verdicts if verdict in {"allow_long", "allow_short"})),
        "intraday_blocks": int(sum(1 for verdict in verdicts if verdict == "block")),
        "latest_intraday_at": latest,
    }


def _dedupe_auto_open_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for candidate in group:
            symbol = str(candidate.get("symbol") or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            out.append(candidate)
    return out


def tick(
    root: Path | None = None,
    *,
    refresh_func: Callable[[], dict[str, Any]] = refresh_order_tracking,
    broker_open_orders_func: Callable[[Any], int] | None = None,
    intraday_decision_loader: Callable[[], dict[str, Any]] = load_intraday_decision_summary,
    monitor_decision_loader: Callable[[Path | None], dict[str, Any]] = load_monitor_decision_summary,
    autopilot_decision_applier: Callable[[Path | None, pd.DataFrame, dict[str, Any]], dict[str, Any]] = apply_paper_autopilot_decisions,
    eod_runner: Callable[[pd.DataFrame, dict[str, Any], int], dict[str, Any]] | None = None,
    auto_open_applier: Callable[[list[dict[str, Any]], list[dict[str, Any]], str], dict[str, Any]] | None = None,
    stale_entry_maintainer: Callable[[], dict[str, Any]] | None = None,
    strong_candidate_loader: Callable[[], list[dict[str, Any]]] = latest_strong_candidates,
    fallback_candidate_loader: Callable[[], list[dict[str, Any]]] = latest_flat_account_fallback_candidates,
    near_miss_candidate_loader: Callable[[], list[dict[str, Any]]] | None = None,
    per_symbol_forecast_candidate_loader: Callable[[], list[dict[str, Any]]] | None = None,
    plan_candidate_loader: Callable[[], list[dict[str, Any]]] | None = None,
    auto_rotation_applier: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
    allow_auto_open: bool = True,
) -> dict[str, Any]:
    """Advance Paper Autopilot by one safe tracking step.

    The first implementation deliberately tracks broker/position state and
    terminates cleanly. It does not auto-apply close/rotate/resize decisions.
    """
    state = load_state(root)
    if state.get("status") != "running":
        state.update({"last_tick_at": _now(), "last_error": "autopilot_not_running"})
        state = save_state(state, root)
        append_tick_log(state, root)
        return state

    cfg = alpaca_config()
    if cfg.live_trading_enabled:
        state = stop(root, reason="live_trading_enabled_guardrail")
        append_tick_log(state, root)
        return state

    stamp = _now()
    try:
        refreshed = refresh_func()
        tracking = _read_csv(refreshed.get("tracking_path"))
        positions = attach_holding_review_to_positions(_read_csv(refreshed.get("positions_path")), root)
        tracked_open_orders = _count_open_orders(tracking)
        broker_open_orders = (broker_open_orders_func or _count_broker_open_orders)(cfg)
        intraday_summary = intraday_decision_loader()
        open_orders = max(tracked_open_orders, broker_open_orders)
        open_positions = int(len(positions))
        stale_entry_result = {
            "stale_entry_open_orders": 0,
            "stale_entry_candidates": 0,
            "stale_entry_canceled": 0,
            "stale_entry_skipped": 0,
            "stale_entry_error": 0,
            "stale_entry_notes": "",
            "stale_entry_path": "",
        }
        if state.get("mode") == "paper_autopilot" and open_orders > 0:
            stale_entry_result = (stale_entry_maintainer or cancel_stale_entry_orders)()
            if int(stale_entry_result.get("stale_entry_canceled") or 0) > 0:
                refreshed = refresh_func()
                tracking = _read_csv(refreshed.get("tracking_path"))
                positions = attach_holding_review_to_positions(_read_csv(refreshed.get("positions_path")), root)
                tracked_open_orders = _count_open_orders(tracking)
                broker_open_orders = (broker_open_orders_func or _count_broker_open_orders)(cfg)
                open_orders = max(tracked_open_orders, broker_open_orders)
                open_positions = int(len(positions))
        update_position_peaks(state, positions)
        if open_positions > 0 and load_monitor_decisions(root).empty:
            build_fresh_monitor_decisions(root, positions, now=datetime.now(timezone.utc))
        monitor_summary = monitor_decision_loader(root)
        eod_result = {
            "eod_state": "inactive",
            "eod_actions": 0,
            "eod_flatten_submitted": 0,
            "eod_remaining": open_positions,
            "eod_banner": "",
            "eod_action_notes": "",
        }
        autopilot_result = {
            "autopilot_actions": 0,
            "autopilot_close_submitted": 0,
            "autopilot_defensive_close_submitted": 0,
            "autopilot_hard_stop_submitted": 0,
            "autopilot_health_close_submitted": 0,
            "autopilot_trailing_close_submitted": 0,
            "autopilot_replace_close_submitted": 0,
            "autopilot_action_notes": "",
            "autopilot_open_attempted": 0,
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "",
        }
        if state.get("mode") == "paper_autopilot" and open_orders == 0 and open_positions > 0:
            autopilot_result = autopilot_decision_applier(root, positions, state)
            if int(autopilot_result.get("autopilot_close_submitted") or 0) > 0:
                open_orders = max(open_orders, int(autopilot_result.get("autopilot_close_submitted") or 0))
        if state.get("mode") == "paper_autopilot" and open_positions > 0:
            if eod_runner is not None:
                eod_result = eod_runner(positions, state, open_orders)
            else:
                eod_result = run_eod_tick(
                    positions,
                    now=datetime.now(timezone.utc),
                    state=state,
                    monitor_decisions=load_monitor_decisions(root),
                    open_orders=open_orders,
                )
            if int(eod_result.get("eod_flatten_submitted") or 0) > 0:
                open_orders = max(open_orders, int(eod_result.get("eod_flatten_submitted") or 0))
        auto_open_result = {
            "autopilot_open_attempted": 0,
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "",
            "basket_state": "normal",
            "red_position_pct": 0.0,
            "basket_return": 0.0,
            "new_entries_paused": False,
            "basket_risk_reason": "",
            "basket_risk_reason_text": "",
        }
        auto_rotation_result = {
            "auto_rotations_attempted": 0,
            "auto_rotations_confirmed": 0,
            "auto_rotation_notes": "",
            "auto_edge_replacements_attempted": 0,
            "auto_edge_replacements_confirmed": 0,
            "auto_edge_replacements_blocked": 0,
            "auto_edge_replacements_dry_run": 0,
        }
        basket = evaluate_basket_risk(
            positions.fillna("").to_dict("records") if not positions.empty else [],
            config=load_basket_risk_config(root),
            previous_state=str(state.get("basket_state") or ""),
        )
        auto_open_result.update(
            {
                "basket_state": basket.basket_state,
                "red_position_pct": basket.red_position_pct,
                "basket_return": basket.basket_return,
                "new_entries_paused": basket.new_entries_paused,
                "basket_risk_reason": basket.reason,
                "basket_risk_reason_text": basket.reason_text,
            }
        )
        eod_state = str(eod_result.get("eod_state") or "inactive")
        if (
            allow_auto_open
            and state.get("mode") == "paper_autopilot"
            and open_orders == 0
            and open_positions > 0
            and eod_state == "inactive"
        ):
            positions_records = positions.fillna("").to_dict("records") if not positions.empty else []
            if auto_rotation_applier is not None:
                auto_rotation_result = auto_rotation_applier(positions_records)
            else:
                auto_rotation_result = apply_auto_rotations(positions_records, root=root)
            if int(auto_rotation_result.get("auto_rotations_confirmed") or 0) > 0:
                open_orders = max(open_orders, int(auto_rotation_result.get("auto_rotations_confirmed") or 0))
        if not allow_auto_open:
            auto_open_result["autopilot_open_notes"] = "auto_open_skipped_market_closed"
        elif basket.new_entries_paused:
            auto_open_result["autopilot_open_notes"] = "basket_new_entries_paused"
        elif state.get("mode") == "paper_autopilot" and open_orders == 0 and eod_state == "inactive":
            positions_records = positions.fillna("").to_dict("records") if not positions.empty else []
            strong_candidates = strong_candidate_loader()
            if per_symbol_forecast_candidate_loader is not None:
                per_symbol_candidates = per_symbol_forecast_candidate_loader()
            else:
                per_symbol_candidates = latest_per_symbol_forecast_fallback_candidates(root=root)
            if near_miss_candidate_loader is not None:
                near_miss_candidates = near_miss_candidate_loader()
            else:
                near_miss_candidates = latest_near_miss_fallback_candidates(root=root)
            ranked_candidates = ranked_fallback_candidates(per_symbol_candidates, near_miss_candidates)
            if plan_candidate_loader is not None:
                plan_candidates = plan_candidate_loader()
            else:
                plan_candidates = latest_plan_fallback_candidates(root=root)
            candidates = _dedupe_auto_open_candidates(strong_candidates, ranked_candidates, plan_candidates)
            if not candidates and open_positions == 0:
                candidates = fallback_candidate_loader()
            if auto_open_applier is not None:
                auto_open_result = auto_open_applier(candidates, positions_records, str(state.get("mode") or "observe"))
            else:
                auto_open_result = apply_auto_open(candidates, positions_records, mode=str(state.get("mode") or "observe"), root=root)
            if int(auto_open_result.get("autopilot_open_submitted") or 0) > 0:
                submitted_opens = int(auto_open_result.get("autopilot_open_submitted") or 0)
                open_orders = max(open_orders, submitted_opens)
                broker_open_orders = max(broker_open_orders, submitted_opens)
                post_open_refreshed = refresh_func()
                post_tracking = _read_csv(post_open_refreshed.get("tracking_path"))
                post_positions = _read_csv(post_open_refreshed.get("positions_path"))
                post_tracked_open_orders = _count_open_orders(post_tracking)
                post_broker_open_orders = (broker_open_orders_func or _count_broker_open_orders)(cfg)
                post_open_orders = max(post_tracked_open_orders, post_broker_open_orders)
                post_open_positions = int(len(post_positions))
                if post_open_orders > 0 or post_open_positions > open_positions:
                    if post_open_positions > open_positions:
                        try:
                            generate_holding_period_report(root, position_file=Path(post_open_refreshed.get("positions_path")))
                        except Exception:
                            pass
                        post_positions = attach_holding_review_to_positions(post_positions, root)
                    refreshed = post_open_refreshed
                    tracking = post_tracking
                    positions = post_positions
                    tracked_open_orders = post_tracked_open_orders
                    broker_open_orders = post_broker_open_orders
                    open_orders = post_open_orders
                    open_positions = post_open_positions
                    update_position_peaks(state, positions)
        submitted_close_actions = max(
            int(autopilot_result.get("autopilot_close_submitted") or 0),
            int(autopilot_result.get("autopilot_replace_close_submitted") or 0),
            int(eod_result.get("eod_flatten_submitted") or 0),
            int(auto_rotation_result.get("auto_rotations_confirmed") or 0),
        )
        in_flight_actions = max(open_orders, submitted_close_actions)
        open_orders = max(open_orders, submitted_close_actions)
        if in_flight_actions > 0:
            phase = "waiting_for_fills"
            status = "running"
            termination_reason = ""
        elif open_positions > 0:
            phase = "monitoring_positions"
            status = "running"
            termination_reason = ""
        elif state.get("mode") == "paper_autopilot":
            phase = "tracking_orders"
            status = "running"
            termination_reason = ""
        else:
            phase = "cycle_complete"
            status = "complete"
            termination_reason = "no_open_orders_or_positions"
        state.update(
            {
                "status": status,
                "phase": phase,
                "updated_at": stamp,
                "last_tick_at": stamp,
                "last_error": "",
                "termination_reason": termination_reason,
                "open_orders": open_orders,
                "broker_open_orders": broker_open_orders,
                "tracked_open_orders": tracked_open_orders,
                "open_positions": open_positions,
                "orders_tracked": int(refreshed.get("orders_tracked") or 0),
                "tracking_path": str(refreshed.get("tracking_path") or ""),
                "positions_path": str(refreshed.get("positions_path") or ""),
                **intraday_summary,
                **monitor_summary,
                **autopilot_result,
                **auto_rotation_result,
                **auto_open_result,
                **stale_entry_result,
                **eod_result,
                "live_trading_enabled": False,
            }
        )
    except Exception as exc:
        state.update({"status": "stopped", "phase": "error", "updated_at": stamp, "stopped_at": stamp, "last_error": str(exc), "termination_reason": "autopilot_error"})
    state = save_state(state, root)
    append_tick_log(state, root)
    return state


def action(action_name: str, root: Path | None = None) -> dict[str, Any]:
    clean = str(action_name or "").strip().lower()
    if clean.startswith("mode:"):
        return set_mode(clean.split(":", 1)[1], root)
    if clean == "start":
        return start(root)
    if clean == "pause":
        return pause(root)
    if clean == "resume":
        return resume(root)
    if clean == "stop":
        return stop(root)
    if clean == "tick":
        return tick(root)
    state = load_state(root)
    state.update({"last_error": f"unsupported_autopilot_action:{clean}", "updated_at": _now()})
    return save_state(state, root)


def context(root: Path | None = None) -> dict[str, Any]:
    if root is None:
        ensure_data_dirs()
    state = load_state(root)
    mode = str(state.get("mode") or "observe")
    mode_meta = AUTOPILOT_MODES.get(mode, AUTOPILOT_MODES["observe"])
    auto_open_config = load_auto_open_config(root=root)
    labels = {
        "idle": "Autopilot Idle",
        "running": "Autopilot Running",
        "paused": "Autopilot Paused",
        "stopped": "Autopilot Stopped",
        "complete": "Cycle Complete",
    }
    phase_labels = {
        "idle": "Idle",
        "tracking_orders": "Tracking Orders",
        "waiting_for_fills": "Waiting for Fills",
        "monitoring_positions": "Monitoring Positions",
        "cycle_complete": "Cycle Complete",
        "paused": "Paused",
        "stopped": "Stopped by Operator",
        "guardrail_stop": "Guardrail Stop",
        "error": "Error",
    }
    return {
        **state,
        "mode": mode,
        "mode_label": mode_meta["label"],
        "mode_summary": mode_meta["summary"],
        "mode_execution_policy": mode_meta["execution_policy"],
        "auto_open_enabled": auto_open_config.open_enabled,
        "auto_rotate_enabled": auto_open_config.rotate_enabled,
        "auto_open_max_per_day": auto_open_config.max_auto_opens_per_day,
        "auto_open_max_positions": auto_open_config.max_positions,
        "flat_account_fallback_max_per_day": auto_open_config.flat_account_fallback_max_per_day,
        "near_miss_fallback_max_per_day": auto_open_config.near_miss_fallback_max_per_day,
        "per_symbol_forecast_fallback_max_per_day": auto_open_config.per_symbol_forecast_fallback_max_per_day,
        "max_position_loss_pct": auto_open_config.max_position_loss_pct,
        "signal_flip_confirmation_clocks": auto_open_config.signal_flip_confirmation_clocks,
        "stale_unknown_loss_close_pct": auto_open_config.stale_unknown_loss_close_pct,
        "trailing_profit_arm_pct": auto_open_config.trailing_profit_arm_pct,
        "trailing_profit_giveback_pct": auto_open_config.trailing_profit_giveback_pct,
        "basket_drawdown_pause_pct": auto_open_config.basket_drawdown_pause_pct,
        "max_long_positions": auto_open_config.max_long_positions,
        "max_short_positions": auto_open_config.max_short_positions,
        "near_miss_entries_with_open_positions": auto_open_config.near_miss_entries_with_open_positions,
        "close_automation_mode": auto_open_config.close_automation_mode,
        "mode_options": mode_options(),
        "capability_rows": capability_rows(),
        "rule_rows": rule_rows(),
        "status_label": labels.get(str(state.get("status") or ""), str(state.get("status") or "idle").replace("_", " ").title()),
        "phase_label": phase_labels.get(str(state.get("phase") or ""), str(state.get("phase") or "idle").replace("_", " ").title()),
        "state_path": str(_state_path(root)),
        "recent_ticks": recent_tick_logs(root),
    }
