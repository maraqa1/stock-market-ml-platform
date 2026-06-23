from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.common.paths import AGENT_DECISIONS_DIR, PROJECT_ROOT, ensure_data_dirs, timestamp
from stockml.services.events import position_id_for_symbol, record_event_once, record_event_safely
from stockml.trading.activity_journal import enrich_exit_activity_details, enrich_monitor_activity_details


LOGGER = logging.getLogger(__name__)
MONITOR_CONFIG_PATH = PROJECT_ROOT / "config" / "monitor.yaml"
HARD_STOP_LOSS_THRESHOLD = -0.04


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
    "trading_stream",
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
    "replacement_score",
    "replacement_edge_bps",
    "replacement_quality_status",
    "replacement_risk_tier",
    "replacement_selection_method",
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
    holding_review: pd.DataFrame | None = None,
    *,
    now: datetime | None = None,
    signal_ttl_minutes: int = 10,
    fallback_signal_time: datetime | None = None,
    min_replacement_rank_improvement: int | None = None,
) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(columns=DECISION_COLUMNS)

    now = now or datetime.now(timezone.utc)
    rotation_config = _rotation_config()
    if min_replacement_rank_improvement is None:
        min_replacement_rank_improvement = int(rotation_config["min_rank_improvement"])
    min_score_delta = float(rotation_config["min_score_delta"])
    plan = plan.copy() if plan is not None and not plan.empty else pd.DataFrame(columns=["symbol"])
    results = results.copy() if results is not None and not results.empty else pd.DataFrame(columns=["symbol"])
    candidate_pool = candidate_pool.copy() if candidate_pool is not None and not candidate_pool.empty else pd.DataFrame(columns=["symbol"])
    holding_review = holding_review.copy() if holding_review is not None and not holding_review.empty else pd.DataFrame(columns=["symbol"])
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

    if not holding_review.empty:
        holding_review["symbol"] = holding_review["symbol"].astype(str).str.upper()
        review_keep = [
            col
            for col in [
                "symbol",
                "trading_stream",
                "recommended_holding_days",
                "review_after_days",
                "max_holding_days",
                "holding_quality",
                "holding_gate_reason",
            ]
            if col in holding_review.columns
        ]
        if review_keep:
            frame = frame.merge(
                holding_review[review_keep].drop_duplicates("symbol", keep="last"),
                on="symbol",
                how="left",
                suffixes=("", "_holding"),
            )

    if not results.empty:
        results = results.copy()
        results["symbol"] = results["symbol"].astype(str).str.upper()
        keep = [col for col in ["symbol", "submitted_at", "updated_at", "filled_avg_price"] if col in results.columns]
        frame = frame.merge(results[keep].drop_duplicates("symbol", keep="last"), on="symbol", how="left", suffixes=("", "_result"))

    decisions = []
    replacement_pool = _replacement_pool(candidate_pool)
    open_positions = positions.copy()
    for _, row in frame.iterrows():
        side = _text(row.get("side")) or _text(row.get("side_plan")) or _text(row.get("position_side")) or "long"
        latest_signal = _text(row.get("trade_action")) or _text(row.get("trade_action_pool")) or "Unknown"
        current_rank = _num(row.get("candidate_rank"), default=float("nan"))
        current_score = _score_value(row)
        current_price = _num(row.get("current_price") or row.get("last_price"))
        avg_entry = _num(row.get("avg_entry_price") or row.get("filled_avg_price"))
        unrealized_plpc = _num(row.get("unrealized_plpc"))
        stop_loss = _num(row.get("stop_loss_price"), default=float("nan"))
        take_profit = _num(row.get("take_profit_price"), default=float("nan"))
        holding_review_max = row.get("max_holding_days_holding")
        max_holding_days = _num(holding_review_max if _text(holding_review_max) else row.get("max_holding_days"), default=10.0)
        trading_stream = _text(row.get("trading_stream")) or ("same_day" if max_holding_days <= 1 else "multi_day")
        signal_age = _signal_age_minutes(row, now, fallback_signal_time)
        holding_days = _holding_days(row, now)

        reasons: list[str] = []
        decision = "hold"
        action = "keep_position"
        is_short = side.lower() in {"short", "sell"}
        desired_action = "Short" if is_short else "Long"
        replacement = find_replacement(
            _text(row.get("symbol")),
            replacement_pool,
            open_positions,
            position_bias=desired_action,
            current_rank=current_rank,
            current_score=current_score,
            min_rank_improvement=min_replacement_rank_improvement,
            min_score_delta=min_score_delta,
        )
        replacement_symbol = _text(replacement.get("symbol")) if replacement is not None else ""
        replacement_side = _text(replacement.get("side")) if replacement is not None else ""
        replacement_rank = _num(replacement.get("candidate_rank"), default=float("nan")) if replacement is not None else float("nan")
        replacement_reason = ""
        replacement_selection_method = "rank" if replacement is not None else ""

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

        if unrealized_plpc <= HARD_STOP_LOSS_THRESHOLD:
            decision, action = "close", "close_position"
            reasons.append("hard_stop_loss_triggered")

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

        edge_replacement = (
            find_edge_replacement(
                _text(row.get("symbol")),
                replacement_pool,
                open_positions,
                position_bias=desired_action,
            )
            if _should_seek_edge_replacement(row, decision, reasons, unrealized_plpc)
            else None
        )
        if edge_replacement is not None:
            replacement = edge_replacement
            replacement_symbol = _text(replacement.get("symbol"))
            replacement_side = _text(replacement.get("side"))
            replacement_rank = _num(replacement.get("candidate_rank"), default=float("nan"))
            replacement_selection_method = "edge"
            decision, action = "replace", "review_edge_replacement"
            replacement_reason = "stronger_edge_candidate_available"
            reasons.append("replacement_edge_improvement")
        elif replacement is not None:
            if decision == "close":
                decision, action = "replace", "close_then_open_replacement"
                replacement_reason = "current_position_exit_with_available_replacement"
                reasons.append("replacement_available")
            elif decision in {"hold", "watch"} and pd.notna(current_rank) and replacement_rank + min_replacement_rank_improvement <= current_rank:
                decision, action = "replace", "close_then_open_replacement"
                replacement_reason = "materially_better_candidate_available"
                reasons.append("replacement_rank_improvement")
        elif decision in {"hold", "watch"} and pd.notna(current_rank):
            decision, action = "watch", "rescore_before_add_or_hold"
            reasons.append("no_eligible_replacement_available")

        if not reasons:
            reasons.append("position_within_rules")

        replacement_score = _score_value(replacement) if replacement is not None else None
        replacement_edge = _candidate_directional_edge_bps(replacement, desired_action) if replacement is not None else None
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
                "unrealized_plpc": unrealized_plpc,
                "latest_signal": latest_signal,
                "trading_stream": trading_stream,
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
                "replacement_score": round(replacement_score, 6) if replacement_score is not None else "",
                "replacement_edge_bps": round(replacement_edge, 2) if replacement_edge is not None else "",
                "replacement_quality_status": _text(replacement.get("trade_quality_status")) if replacement is not None else "",
                "replacement_risk_tier": _text(replacement.get("risk_tier")) if replacement is not None else "",
                "replacement_selection_method": replacement_selection_method,
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


def find_replacement(
    symbol_to_replace: str,
    shortlist: pd.DataFrame | list[dict[str, Any]],
    open_positions: pd.DataFrame | list[dict[str, Any]],
    min_rank_improvement: int | None = None,
    *,
    position_bias: str | None = None,
    current_rank: float | None = None,
    current_score: float | None = None,
    min_score_delta: float | None = None,
) -> pd.Series | None:
    """Select a rotation candidate only after consulting open positions.

    Rotation candidates must exclude names already held in open positions.
    This is a safety requirement; do not bypass this helper with raw
    ``shortlist.iloc[0]`` selection.
    """

    cfg = _rotation_config()
    min_rank = int(min_rank_improvement if min_rank_improvement is not None else cfg["min_rank_improvement"])
    min_score = float(min_score_delta if min_score_delta is not None else cfg["min_score_delta"])
    symbol = _text(symbol_to_replace).upper()
    pool = shortlist.copy() if isinstance(shortlist, pd.DataFrame) else pd.DataFrame(shortlist)
    positions = open_positions.copy() if isinstance(open_positions, pd.DataFrame) else pd.DataFrame(open_positions)
    considered = int(len(pool))
    rejected = {"held": 0, "wrong_bias": 0, "insufficient_rank": 0, "insufficient_score": 0}

    if pool.empty or "symbol" not in pool.columns:
        _log_replacement_search(symbol, considered, rejected, None)
        return None

    pool = pool.copy()
    pool["symbol"] = pool["symbol"].astype(str).str.upper()
    if "candidate_rank" not in pool.columns:
        pool["candidate_rank"] = range(1, len(pool) + 1)
    pool = pool.sort_values("candidate_rank")
    held_symbols = _held_symbols(positions)
    desired = _normal_bias(position_bias)
    current_rank_value = _optional_float(current_rank)
    current_score_value = _optional_float(current_score)

    for _, candidate in pool.iterrows():
        candidate_symbol = _text(candidate.get("symbol")).upper()
        if not candidate_symbol or candidate_symbol == symbol:
            rejected["held"] += 1
            continue
        if candidate_symbol in held_symbols:
            rejected["held"] += 1
            continue
        candidate_bias = _normal_bias(candidate.get("trade_action") or candidate.get("bias") or candidate.get("side"))
        if desired and candidate_bias and candidate_bias != desired:
            rejected["wrong_bias"] += 1
            continue
        candidate_rank = _num(candidate.get("candidate_rank"), default=float("nan"))
        if current_rank_value is not None and (pd.isna(candidate_rank) or candidate_rank + min_rank > current_rank_value):
            rejected["insufficient_rank"] += 1
            continue
        candidate_score = _score_value(candidate)
        if current_score_value is not None and candidate_score is not None and candidate_score - current_score_value < min_score:
            rejected["insufficient_score"] += 1
            continue
        _log_replacement_search(symbol, considered, rejected, candidate_symbol)
        return candidate

    _log_replacement_search(symbol, considered, rejected, None)
    return None


def find_edge_replacement(
    symbol_to_replace: str,
    shortlist: pd.DataFrame | list[dict[str, Any]],
    open_positions: pd.DataFrame | list[dict[str, Any]],
    *,
    position_bias: str | None = None,
) -> pd.Series | None:
    """Select the strongest non-held same-side candidate for weak held names.

    This selector is deliberately separate from ``find_replacement`` so the
    legacy rank-rotation path remains rank-first. The edge selector only feeds
    operator-review recommendations for positions already deemed weak by the
    monitor; it does not submit or resize orders.
    """

    symbol = _text(symbol_to_replace).upper()
    pool = shortlist.copy() if isinstance(shortlist, pd.DataFrame) else pd.DataFrame(shortlist)
    positions = open_positions.copy() if isinstance(open_positions, pd.DataFrame) else pd.DataFrame(open_positions)
    if pool.empty or "symbol" not in pool.columns:
        return None

    pool = pool.copy()
    pool["symbol"] = pool["symbol"].astype(str).str.upper()
    if "candidate_rank" not in pool.columns:
        pool["candidate_rank"] = range(1, len(pool) + 1)
    desired = _normal_bias(position_bias)
    held = _held_symbols(positions)
    status = pool.get("trade_quality_status", pd.Series("", index=pool.index)).astype(str).str.lower()
    eligible = pool.get("order_eligible", pd.Series(False, index=pool.index)).map(_bool_value)
    qty = pd.to_numeric(pool.get("suggested_quantity", pd.Series(0, index=pool.index)), errors="coerce").fillna(0)
    pool = pool[status.isin({"approved", "reduced"}) & eligible & (qty >= 1)].copy()
    if pool.empty:
        return None

    pool["__held"] = pool["symbol"].isin(held) | pool["symbol"].eq(symbol)
    pool["__bias"] = pool.apply(lambda row: _normal_bias(row.get("trade_action") or row.get("bias") or row.get("side")), axis=1)
    pool["__edge_bps"] = pool.apply(lambda row: _candidate_directional_edge_bps(row, desired), axis=1)
    pool["__score"] = pool.apply(lambda row: _score_value(row), axis=1)
    pool["__quality"] = pool.apply(_edge_replacement_quality_priority, axis=1)
    pool["__rank"] = pd.to_numeric(pool["candidate_rank"], errors="coerce").fillna(999999)
    pool = pool[
        ~pool["__held"]
        & pool["__edge_bps"].gt(0)
        & (pool["__bias"].eq(desired) if desired else pool["__bias"].isin({"long", "short"}))
    ].copy()
    if pool.empty:
        return None

    pool = pool.sort_values(
        ["__quality", "__edge_bps", "__score", "__rank", "symbol"],
        ascending=[True, False, False, True, True],
        na_position="last",
    )
    return pool.iloc[0]


def _should_seek_edge_replacement(row: pd.Series, decision: str, reasons: list[str], unrealized_plpc: float) -> bool:
    if decision not in {"hold", "watch"}:
        return False
    quality = _text(row.get("holding_quality")).lower()
    holding_reason = _text(row.get("holding_gate_reason")).lower()
    if quality in {"strong", "healthy"} and unrealized_plpc > 0:
        return False
    if quality in {"avoid", "weak"}:
        return True
    if "holding_edge_not_confirmed" in holding_reason:
        return True
    weak_reasons = {"latest_signal_unknown", "signal_stale", "signal_age_unknown"}
    return bool(weak_reasons.intersection(reasons) and unrealized_plpc <= 0)


def _candidate_directional_edge_bps(row: pd.Series | dict[str, Any] | None, desired_bias: str | None = None) -> float:
    if row is None:
        return 0.0
    desired = _normal_bias(desired_bias)
    candidate_bias = _normal_bias(_row_get(row, "trade_action") or _row_get(row, "bias") or _row_get(row, "side"))
    sign = -1.0 if (desired or candidate_bias) == "short" else 1.0
    for column in [
        "expected_trade_return",
        "expected_return",
        "forward_5d_alpha_vs_sector",
        "forward_5d_alpha_vs_spy",
        "forward_5d_return",
        "probability_edge",
    ]:
        value = _optional_float(_row_get(row, column))
        if value is not None:
            return sign * _return_like_to_bps(value)
    return 0.0


def _return_like_to_bps(value: float) -> float:
    magnitude = abs(value)
    if magnitude <= 1:
        return value * 10000.0
    if magnitude <= 20:
        return value * 100.0
    return value


def _edge_replacement_quality_priority(row: pd.Series) -> int:
    status = _text(row.get("trade_quality_status")).lower()
    tier = _text(row.get("risk_tier")).lower()
    if status == "approved" and tier == "high_quality":
        return 0
    if status == "approved":
        return 1
    if status == "reduced" and tier == "high_quality":
        return 2
    if status == "reduced" and tier == "medium":
        return 3
    if status == "reduced":
        return 4
    return 5


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n", ""}:
        return False
    return bool(value)


def _row_get(row: pd.Series | dict[str, Any], column: str) -> object:
    try:
        return row.get(column)  # type: ignore[union-attr]
    except Exception:
        return None


def _rotation_config() -> dict[str, float | int]:
    payload: dict[str, Any] = {}
    if MONITOR_CONFIG_PATH.exists():
        try:
            payload = yaml.safe_load(MONITOR_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            LOGGER.exception("failed_to_read_monitor_rotation_config")
    rotation = payload.get("rotation") if isinstance(payload, dict) else {}
    if not isinstance(rotation, dict):
        rotation = {}
    return {
        "min_rank_improvement": int(rotation.get("min_rank_improvement", 3)),
        "min_score_delta": float(rotation.get("min_score_delta", 0.02)),
    }


def _held_symbols(open_positions: pd.DataFrame) -> set[str]:
    if open_positions.empty or "symbol" not in open_positions.columns:
        return set()
    frame = open_positions.copy()
    if "status" in frame.columns:
        status = frame["status"].fillna("open").astype(str).str.lower()
        frame = frame[status.eq("open") | status.eq("")]
    return {str(symbol).upper() for symbol in frame["symbol"].dropna() if _text(symbol)}


def _normal_bias(value: object) -> str:
    text = _text(value).lower()
    if text in {"long", "buy", "l"}:
        return "long"
    if text in {"short", "sell", "s"}:
        return "short"
    return ""


def _score_value(row: pd.Series | dict[str, Any]) -> float | None:
    for column in ["score", "confidence_score", "risk_adjusted_score", "side_probability"]:
        try:
            value = row.get(column)  # type: ignore[union-attr]
        except Exception:
            value = None
        number = _optional_float(value)
        if number is not None:
            return number
    return None


def _optional_float(value: object) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return float(number)


def _log_replacement_search(symbol: str, considered: int, rejected: dict[str, int], returned: str | None) -> None:
    LOGGER.info(
        "rotation_replacement_search",
        extra={
            "symbol": symbol,
            "shortlist_considered": considered,
            "rejected_held": rejected.get("held", 0),
            "rejected_wrong_bias": rejected.get("wrong_bias", 0),
            "rejected_insufficient_rank": rejected.get("insufficient_rank", 0),
            "rejected_insufficient_score": rejected.get("insufficient_score", 0),
            "returned_symbol": returned or "",
        },
    )


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
        details = {
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
        }
        if event_type == "monitor_rotate":
            replacement = _text(row.get("replacement_symbol")).upper()
            details_summary = _text(row.get("details_summary") or row.get("decision_reason") or row.get("replacement_reason") or row.get("recommended_action"))
            key = f"monitor_rotate:{symbol}:{replacement}:{row.get('recommended_action')}:{details_summary}"
            details.update({"event_key": key, "details_summary": details_summary, "skipped_reason": "monitor_action_cooldown_active"})
            record_event_once(
                position_id_for_symbol(symbol),
                event_type,
                "position_monitor",
                enrich_monitor_activity_details(symbol, details),
                event_key=key,
                cooldown_seconds=30 * 60,
            )
        else:
            record_event_safely(
                position_id_for_symbol(symbol),
                event_type,
                "position_monitor",
                enrich_exit_activity_details(symbol, details, reason=details.get("decision_reason") or details.get("recommended_action")) if event_type == "monitor_close" else enrich_monitor_activity_details(symbol, details),
            )
    return path
