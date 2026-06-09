from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.autopilot.open import load_auto_open_config
from stockml.common.paths import OPERATOR_ACTIONS_DIR
from stockml.trading.config import AlpacaConfig, alpaca_config
from stockml.trading.manual_position_actions import apply_manual_position_action


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def monitor_close_candidates(decisions: pd.DataFrame) -> pd.DataFrame:
    if decisions.empty:
        return pd.DataFrame(columns=decisions.columns)
    frame = decisions.copy()
    if "symbol" not in frame.columns:
        frame["symbol"] = ""
    decision = frame.get("decision", pd.Series("", index=frame.index)).map(_text).str.lower()
    recommended = frame.get("recommended_action", pd.Series("", index=frame.index)).map(_text).str.lower()
    reason = frame.get("decision_reason", pd.Series("", index=frame.index)).map(_text).str.lower()
    symbols = frame["symbol"].map(_text).str.upper()
    close_only = decision.eq("close") & recommended.eq("close_position") & symbols.ne("")
    stop_loss_replace = decision.eq("replace") & recommended.eq("close_then_open_replacement") & reason.str.contains("stop_loss_triggered", regex=False) & symbols.ne("")
    selected = close_only | stop_loss_replace
    out = frame[selected].copy()
    out["symbol"] = symbols.loc[out.index]
    return out.drop_duplicates("symbol", keep="last")


def _today_actions_path() -> Path:
    return OPERATOR_ACTIONS_DIR / f"operator_position_actions_{datetime.now().strftime('%Y%m%d')}.csv"


def _prior_submitted_close_symbols(actions: pd.DataFrame | None = None) -> set[str]:
    frame = actions
    if frame is None:
        path = _today_actions_path()
        frame = pd.read_csv(path, low_memory=False) if path.exists() else pd.DataFrame()
    if frame.empty:
        return set()
    action = frame.get("operator_action", pd.Series("", index=frame.index)).map(_text).str.lower()
    status = frame.get("status", pd.Series("", index=frame.index)).map(_text).str.lower()
    symbols = frame.get("symbol", pd.Series("", index=frame.index)).map(_text).str.upper()
    submitted_close = action.eq("close") & status.eq("submitted") & symbols.ne("")
    return set(symbols[submitted_close].tolist())


def execute_monitor_auto_closes(
    decisions: pd.DataFrame,
    *,
    close_automation_mode: str | None = None,
    config: AlpacaConfig | None = None,
    action_func: Callable[[str, str], dict[str, Any]] | None = None,
    previous_actions: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Submit paper close orders for explicit monitor loss-control decisions.

    This path closes explicit monitor close decisions and replace decisions
    whose current leg has already tripped the stop loss. It never opens the
    suggested replacement; entry remains delegated to separate guarded paths.
    """

    candidates = monitor_close_candidates(decisions)
    mode = (close_automation_mode or load_auto_open_config().close_automation_mode or "automatic").strip().lower()
    if candidates.empty:
        return {
            "auto_close_status": "no_candidates",
            "auto_close_candidates": 0,
            "auto_close_attempted": 0,
            "auto_close_submitted": 0,
            "auto_close_dry_run": 0,
            "auto_close_rejected": 0,
            "auto_close_error": 0,
            "auto_close_notes": "",
        }
    if mode != "automatic":
        return {
            "auto_close_status": "skipped",
            "auto_close_reason": f"close_automation_mode:{mode}",
            "auto_close_candidates": len(candidates),
            "auto_close_attempted": 0,
            "auto_close_submitted": 0,
            "auto_close_dry_run": 0,
            "auto_close_rejected": 0,
            "auto_close_error": 0,
            "auto_close_notes": "",
        }

    cfg = config or alpaca_config()
    already_submitted = _prior_submitted_close_symbols(previous_actions)
    candidate_count = len(candidates)
    candidates = candidates[~candidates["symbol"].isin(already_submitted)].copy()
    skipped_existing = candidate_count - len(candidates)
    apply_action = action_func or (lambda symbol, action: apply_manual_position_action(symbol, action, config=cfg))
    submitted = 0
    dry_run = 0
    rejected = 0
    errors = 0
    notes: list[str] = []
    for row in candidates.to_dict("records"):
        symbol = _text(row.get("symbol")).upper()
        if not symbol:
            continue
        result = apply_action(symbol, "close")
        status = _text(result.get("status")).lower()
        message = _text(result.get("message"))
        if status == "submitted":
            submitted += 1
        elif status == "dry_run":
            dry_run += 1
        elif status == "error":
            errors += 1
        else:
            rejected += 1
        notes.append(f"{symbol}:{status or 'unknown'}:{message or 'no_message'}")

    attempted = submitted + dry_run + rejected + errors
    return {
        "auto_close_status": "ok",
        "auto_close_candidates": candidate_count,
        "auto_close_skipped_existing": skipped_existing,
        "auto_close_attempted": attempted,
        "auto_close_submitted": submitted,
        "auto_close_dry_run": dry_run,
        "auto_close_rejected": rejected,
        "auto_close_error": errors,
        "auto_close_notes": "; ".join(notes[:20]),
    }
