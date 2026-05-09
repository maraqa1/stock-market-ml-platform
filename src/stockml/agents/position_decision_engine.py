from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockml.common.paths import AGENT_DECISIONS_DIR, ensure_data_dirs, timestamp
from stockml.services.events import position_id_for_symbol, record_event_safely


DECISION_COLUMNS = [
    "symbol",
    "side",
    "qty",
    "current_price",
    "avg_entry_price",
    "market_value",
    "cost_basis",
    "unrealized_pl",
    "unrealized_plpc",
    "latest_signal",
    "signal_age_minutes",
    "stop_loss_price",
    "take_profit_price",
    "max_holding_days",
    "decision",
    "recommended_action",
    "decision_reason",
    "replacement_symbol",
    "replacement_side",
    "replacement_rank",
    "replacement_reason",
]


def _num(value: object, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(parsed) else float(parsed)


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _as_time(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _signal_age_minutes(row: pd.Series, now: datetime, fallback_signal_time: datetime | None) -> float | None:
    signal_time = None
    for column in ["signal_generated_at", "generated_at", "created_at", "submitted_at", "date"]:
        if column in row.index:
            signal_time = _as_time(row.get(column))
            if signal_time:
                break
    signal_time = signal_time or fallback_signal_time
    if not signal_time:
        return None
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    return max(0.0, (now - signal_time).total_seconds() / 60.0)


def _holding_days(row: pd.Series, now: datetime) -> float | None:
    opened_at = None
    for column in ["submitted_at", "updated_at", "filled_at"]:
        if column in row.index:
            opened_at = _as_time(row.get(column))
            if opened_at:
                break
    if not opened_at:
        return None
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - opened_at).total_seconds() / 86400.0)


def build_position_decisions(
    positions: pd.DataFrame,
    plan: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
    candidate_pool: pd.DataFrame | None = None,
    *,
    now: datetime | None = None,
    signal_ttl_minutes: int = 10,
    fallback_signal_time: datetime | None = None,
    min_replacement_rank_improvement: int = 10,
) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(columns=DECISION_COLUMNS)

    now = now or datetime.now(timezone.utc)
    plan = plan.copy() if plan is not None and not plan.empty else pd.DataFrame(columns=["symbol"])
    results = results.copy() if results is not None and not results.empty else pd.DataFrame(columns=["symbol"])
    candidate_pool = candidate_pool.copy() if candidate_pool is not None and not candidate_pool.empty else pd.DataFrame(columns=["symbol"])
    frame = positions.copy()
    if "symbol" not in frame.columns:
        frame["symbol"] = ""
    frame["symbol"] = frame["symbol"].astype(str).str.upper()

    if not plan.empty:
        plan = plan.copy()
        plan["symbol"] = plan["symbol"].astype(str).str.upper()
        keep = [
            col
            for col in [
                "symbol",
                "trade_action",
                "signal_generated_at",
                "date",
                "stop_loss_price",
                "take_profit_price",
                "max_holding_days",
                "side",
            ]
            if col in plan.columns
        ]
        frame = frame.merge(plan[keep].drop_duplicates("symbol", keep="last"), on="symbol", how="left", suffixes=("", "_plan"))

    if not candidate_pool.empty:
        candidate_pool["symbol"] = candidate_pool["symbol"].astype(str).str.upper()
        pool_status = candidate_pool.get("trade_quality_status", pd.Series("", index=candidate_pool.index)).astype(str).str.lower()
        pool_eligible = candidate_pool.get("order_eligible", pd.Series(False, index=candidate_pool.index)).astype(bool)
        active_pool = candidate_pool[pool_status.isin({"approved", "reduced"}) & pool_eligible].copy()
        pool_keep = [
            col
            for col in ["symbol", "trade_action", "candidate_rank", "side", "confidence_score", "risk_adjusted_score"]
            if col in active_pool.columns
        ]
        if pool_keep:
            frame = frame.merge(
                active_pool[pool_keep].drop_duplicates("symbol", keep="last"),
                on="symbol",
                how="left",
                suffixes=("", "_pool"),
            )

    if not results.empty:
        results = results.copy()
        results["symbol"] = results["symbol"].astype(str).str.upper()
        keep = [col for col in ["symbol", "submitted_at", "updated_at", "filled_avg_price"] if col in results.columns]
        frame = frame.merge(results[keep].drop_duplicates("symbol", keep="last"), on="symbol", how="left", suffixes=("", "_result"))

    decisions = []
    replacement_pool = _replacement_pool(candidate_pool)
    for _, row in frame.iterrows():
        side = _text(row.get("side")) or _text(row.get("side_plan")) or _text(row.get("position_side")) or "long"
        latest_signal = _text(row.get("trade_action")) or _text(row.get("trade_action_pool")) or "Unknown"
        current_rank = _num(row.get("candidate_rank"), default=float("nan"))
        current_price = _num(row.get("current_price") or row.get("last_price"))
        avg_entry = _num(row.get("avg_entry_price") or row.get("filled_avg_price"))
        stop_loss = _num(row.get("stop_loss_price"), default=float("nan"))
        take_profit = _num(row.get("take_profit_price"), default=float("nan"))
        max_holding_days = _num(row.get("max_holding_days"), default=10.0)
        signal_age = _signal_age_minutes(row, now, fallback_signal_time)
        holding_days = _holding_days(row, now)

        reasons: list[str] = []
        decision = "hold"
        action = "keep_position"
        is_short = side.lower() in {"short", "sell"}
        desired_action = "Short" if is_short else "Long"
        replacement = _best_replacement(replacement_pool, _text(row.get("symbol")), desired_action)
        replacement_symbol = _text(replacement.get("symbol")) if replacement is not None else ""
        replacement_side = _text(replacement.get("side")) if replacement is not None else ""
        replacement_rank = _num(replacement.get("candidate_rank"), default=float("nan")) if replacement is not None else float("nan")
        replacement_reason = ""

        if current_price <= 0:
            decision, action = "watch", "manual_review"
            reasons.append("current_price_missing")
        elif latest_signal == "Unknown":
            decision, action = "watch", "manual_review"
            reasons.append("latest_signal_unknown")
        elif latest_signal not in {"Long", "Short"}:
            decision, action = "close", "close_position"
            reasons.append("signal_no_longer_active")
        elif is_short and latest_signal != "Short":
            decision, action = "close", "close_position"
            reasons.append("short_signal_not_active")
        elif not is_short and latest_signal != "Long":
            decision, action = "close", "close_position"
            reasons.append("long_signal_not_active")

        if current_price > 0 and pd.notna(stop_loss):
            if (not is_short and current_price <= stop_loss) or (is_short and current_price >= stop_loss):
                decision, action = "close", "close_position"
                reasons.append("stop_loss_triggered")

        if current_price > 0 and pd.notna(take_profit):
            if (not is_short and current_price >= take_profit) or (is_short and current_price <= take_profit):
                decision, action = "close", "close_position"
                reasons.append("take_profit_triggered")

        if holding_days is not None and max_holding_days and holding_days >= max_holding_days:
            decision, action = "close", "close_position"
            reasons.append("max_holding_days_exceeded")

        if signal_age is None:
            if decision == "hold":
                decision, action = "watch", "manual_review"
            reasons.append("signal_age_unknown")
        elif signal_age > signal_ttl_minutes and decision == "hold":
            decision, action = "watch", "rescore_before_add_or_hold"
            reasons.append("signal_stale")

        if replacement is not None:
            if decision == "close":
                decision, action = "replace", "close_then_open_replacement"
                replacement_reason = "current_position_exit_with_available_replacement"
                reasons.append("replacement_available")
            elif decision in {"hold", "watch"} and pd.notna(current_rank) and replacement_rank + min_replacement_rank_improvement <= current_rank:
                decision, action = "replace", "close_then_open_replacement"
                replacement_reason = "materially_better_candidate_available"
                reasons.append("replacement_rank_improvement")

        if not reasons:
            reasons.append("position_within_rules")

        decisions.append(
            {
                "symbol": _text(row.get("symbol")),
                "side": side,
                "qty": _num(row.get("qty")),
                "current_price": current_price,
                "avg_entry_price": avg_entry,
                "market_value": _num(row.get("market_value")),
                "cost_basis": _num(row.get("cost_basis")),
                "unrealized_pl": _num(row.get("unrealized_pl")),
                "unrealized_plpc": _num(row.get("unrealized_plpc")),
                "latest_signal": latest_signal,
                "signal_age_minutes": round(signal_age, 2) if signal_age is not None else "",
                "stop_loss_price": round(stop_loss, 4) if pd.notna(stop_loss) else "",
                "take_profit_price": round(take_profit, 4) if pd.notna(take_profit) else "",
                "max_holding_days": int(max_holding_days) if max_holding_days else "",
                "decision": decision,
                "recommended_action": action,
                "decision_reason": "|".join(dict.fromkeys(reasons)),
                "replacement_symbol": replacement_symbol,
                "replacement_side": replacement_side,
                "replacement_rank": int(replacement_rank) if pd.notna(replacement_rank) else "",
                "replacement_reason": replacement_reason,
            }
        )
    return pd.DataFrame(decisions, columns=DECISION_COLUMNS)


def _replacement_pool(candidate_pool: pd.DataFrame) -> pd.DataFrame:
    if candidate_pool.empty or "symbol" not in candidate_pool.columns:
        return pd.DataFrame()
    pool = candidate_pool.copy()
    pool["symbol"] = pool["symbol"].astype(str).str.upper()
    if "candidate_rank" not in pool.columns:
        pool["candidate_rank"] = range(1, len(pool) + 1)
    status = pool.get("trade_quality_status", pd.Series("", index=pool.index)).astype(str).str.lower()
    eligible = pool.get("order_eligible", pd.Series(False, index=pool.index)).astype(bool)
    qty = pd.to_numeric(pool.get("suggested_quantity", pd.Series(0, index=pool.index)), errors="coerce").fillna(0)
    return pool[status.isin({"approved", "reduced"}) & eligible & (qty >= 1)].sort_values("candidate_rank")


def _best_replacement(pool: pd.DataFrame, current_symbol: str, desired_action: str) -> pd.Series | None:
    if pool.empty:
        return None
    candidates = pool[
        pool["symbol"].ne(current_symbol.upper())
        & pool.get("trade_action", pd.Series("", index=pool.index)).astype(str).str.lower().eq(desired_action.lower())
    ]
    if candidates.empty:
        return None
    return candidates.iloc[0]


def write_position_decisions(decisions: pd.DataFrame, stamp: str | None = None) -> Path:
    ensure_data_dirs()
    path = AGENT_DECISIONS_DIR / f"position_decisions_{stamp or timestamp()}.csv"
    decisions.to_csv(path, index=False)
    for row in decisions.to_dict("records"):
        symbol = _text(row.get("symbol"))
        if not symbol:
            continue
        decision = _text(row.get("decision")).lower()
        event_type = {
            "hold": "monitor_safe",
            "watch": "monitor_watch",
            "close": "monitor_close",
            "replace": "monitor_rotate",
        }.get(decision, "monitor_watch")
        record_event_safely(
            position_id_for_symbol(symbol),
            event_type,
            "position_monitor",
            {
                "symbol": symbol,
                "decision": row.get("decision"),
                "recommended_action": row.get("recommended_action"),
                "decision_reason": row.get("decision_reason"),
                "current_price": row.get("current_price"),
                "unrealized_pl": row.get("unrealized_pl"),
                "unrealized_plpc": row.get("unrealized_plpc"),
                "replacement_symbol": row.get("replacement_symbol"),
                "replacement_reason": row.get("replacement_reason"),
                "decision_path": str(path),
            },
        )
    return path
