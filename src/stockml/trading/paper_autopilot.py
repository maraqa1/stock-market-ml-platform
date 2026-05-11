from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import desc, select

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs
from stockml.db.connection import get_engine
from stockml.db.schema import intraday_decisions
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.manual_position_actions import apply_manual_position_action
from stockml.trading.paper_trader import refresh_order_tracking


STATE_VERSION = 1
TERMINAL_ORDER_STATES = {"filled", "canceled", "cancelled", "expired", "rejected"}
OPEN_ORDER_STATES = {"accepted", "new", "pending_new", "pending_replace", "submitted", "partially_filled", "partial"}
DEFENSIVE_STALE_LOSS_THRESHOLD = -0.025
HARD_STOP_LOSS_THRESHOLD = -0.04
TRAILING_PROFIT_MIN = 0.03
TRAILING_GIVEBACK_THRESHOLD = 0.015
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
    "autopilot_trailing_close_submitted",
    "autopilot_action_notes",
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
        "autopilot_trailing_close_submitted": 0,
        "autopilot_action_notes": "",
        "position_peak_plpc": {},
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
            "auto_open": False,
            "auto_rotate": False,
            "description": "Can submit guarded paper close orders from the exit playbook.",
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
            "trigger": f"Signal stale and unrealized return <= {DEFENSIVE_STALE_LOSS_THRESHOLD:.1%}.",
            "action": "Submit paper close order",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Trailing profit protection",
            "trigger": f"Peak return >= {TRAILING_PROFIT_MIN:.1%} and giveback >= {TRAILING_GIVEBACK_THRESHOLD:.1%} on a stale signal.",
            "action": "Submit paper close order",
            "active_in": "Paper Autopilot",
        },
        {
            "rule": "Open candidate",
            "trigger": "Candidate evaluation finds a new possible entry.",
            "action": "Review only",
            "active_in": "No automatic mode",
        },
        {
            "rule": "Rotation",
            "trigger": "A better candidate exists than a current holding.",
            "action": "Review only",
            "active_in": "No automatic mode",
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
    explicit_close = frame["__decision"] == "close"
    hard_stop = frame["__plpc"] <= HARD_STOP_LOSS_THRESHOLD
    defensive_stale_loss = (
        (frame["__decision"] == "watch")
        & frame["__reason"].str.contains("signal_stale", na=False)
        & (frame["__plpc"] <= DEFENSIVE_STALE_LOSS_THRESHOLD)
    )
    trailing_profit = (
        (frame["__decision"] == "watch")
        & frame["__reason"].str.contains("signal_stale", na=False)
        & (frame["__peak_plpc"] >= TRAILING_PROFIT_MIN)
        & ((frame["__peak_plpc"] - frame["__plpc"]) >= TRAILING_GIVEBACK_THRESHOLD)
    )
    out = frame[(explicit_close | hard_stop | defensive_stale_loss | trailing_profit) & frame["__symbol"].isin(open_symbols)].copy()
    out["__autopilot_close_reason"] = "monitor_close"
    out.loc[hard_stop.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "hard_stop_loss"
    out.loc[defensive_stale_loss.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "defensive_stale_loss"
    out.loc[trailing_profit.reindex(out.index, fill_value=False), "__autopilot_close_reason"] = "trailing_profit_giveback"
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
    hard stop-loss breaches, stale losing positions, and stale winners that
    give back too much profit. It deliberately does not auto-open or auto-rotate.
    """
    candidates = _auto_close_candidates(root, positions, state)
    if candidates.empty:
        return {
            "autopilot_actions": 0,
            "autopilot_close_submitted": 0,
            "autopilot_defensive_close_submitted": 0,
            "autopilot_hard_stop_submitted": 0,
            "autopilot_trailing_close_submitted": 0,
            "autopilot_action_notes": "",
        }

    apply_action = action_func or (lambda symbol, action: apply_manual_position_action(symbol, action))
    notes: list[str] = []
    submitted = 0
    defensive_submitted = 0
    hard_stop_submitted = 0
    trailing_submitted = 0
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
            elif reason == "trailing_profit_giveback":
                trailing_submitted += 1
        close_reason = str(row.get("__autopilot_close_reason") or "monitor_close")
        notes.append(f"{symbol}:{close_reason}:{status or 'unknown'}:{message or 'no_message'}")
    return {
        "autopilot_actions": actions,
        "autopilot_close_submitted": submitted,
        "autopilot_defensive_close_submitted": defensive_submitted,
        "autopilot_hard_stop_submitted": hard_stop_submitted,
        "autopilot_trailing_close_submitted": trailing_submitted,
        "autopilot_action_notes": "; ".join(notes[:10]),
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


def tick(
    root: Path | None = None,
    *,
    refresh_func: Callable[[], dict[str, Any]] = refresh_order_tracking,
    broker_open_orders_func: Callable[[Any], int] | None = None,
    intraday_decision_loader: Callable[[], dict[str, Any]] = load_intraday_decision_summary,
    monitor_decision_loader: Callable[[Path | None], dict[str, Any]] = load_monitor_decision_summary,
    autopilot_decision_applier: Callable[[Path | None, pd.DataFrame, dict[str, Any]], dict[str, Any]] = apply_paper_autopilot_decisions,
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
        positions = _read_csv(refreshed.get("positions_path"))
        tracked_open_orders = _count_open_orders(tracking)
        broker_open_orders = (broker_open_orders_func or _count_broker_open_orders)(cfg)
        intraday_summary = intraday_decision_loader()
        monitor_summary = monitor_decision_loader(root)
        open_orders = max(tracked_open_orders, broker_open_orders)
        open_positions = int(len(positions))
        update_position_peaks(state, positions)
        autopilot_result = {
            "autopilot_actions": 0,
            "autopilot_close_submitted": 0,
            "autopilot_defensive_close_submitted": 0,
            "autopilot_hard_stop_submitted": 0,
            "autopilot_trailing_close_submitted": 0,
            "autopilot_action_notes": "",
        }
        if state.get("mode") == "paper_autopilot" and open_orders == 0 and open_positions > 0:
            autopilot_result = autopilot_decision_applier(root, positions, state)
            if int(autopilot_result.get("autopilot_close_submitted") or 0) > 0:
                open_orders = max(open_orders, int(autopilot_result.get("autopilot_close_submitted") or 0))
                broker_open_orders = max(broker_open_orders, int(autopilot_result.get("autopilot_close_submitted") or 0))
        if open_orders > 0:
            phase = "waiting_for_fills"
            status = "running"
            termination_reason = ""
        elif open_positions > 0:
            phase = "monitoring_positions"
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
        "mode_options": mode_options(),
        "capability_rows": capability_rows(),
        "rule_rows": rule_rows(),
        "status_label": labels.get(str(state.get("status") or ""), str(state.get("status") or "idle").replace("_", " ").title()),
        "phase_label": phase_labels.get(str(state.get("phase") or ""), str(state.get("phase") or "idle").replace("_", " ").title()),
        "state_path": str(_state_path(root)),
        "recent_ticks": recent_tick_logs(root),
    }
