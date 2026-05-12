from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import (
    autopilot_open_log,
    daily_report_runs,
    eod_flatten_log,
    eod_summary,
    intraday_promotion_log,
    kill_switch_events,
    position_events,
    rotation_recommendation_log,
)


RECOMMENDATION_TEMPLATES = {
    "MISSED_OPPORTUNITIES_REVIEW": "Missed strong promotions occurred; review promotion thresholds and auto-open gating.",
    "KILL_SWITCH_REVIEW": "Kill-switch activity occurred; review risk limits before the next session.",
    "ROTATION_REVIEW": "Rotation recommendations were proposed; review confirms, overrides, and expirations.",
    "NO_ACTION_REQUIRED": "No report-driven rule changes suggested.",
}
DEFENSIBLE_BLOCK_REASONS = {"earnings_today", "earnings_after_close", "wide_spread", "low_liquidity", "near_open", "near_close", "kill_switch_daily", "kill_switch_weekly", "kill_switch_total"}


@dataclass(frozen=True)
class ReportWindow:
    session_date: date
    start: datetime
    end: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _window(session_date: date) -> ReportWindow:
    start = datetime.combine(session_date, time.min, tzinfo=timezone.utc)
    return ReportWindow(session_date=session_date, start=start, end=start + timedelta(days=1))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except Exception:
        return default


def _rows(engine: Engine, table, timestamp_column, window: ReportWindow) -> list[dict[str, Any]]:
    try:
        with engine.connect() as conn:
            return list(
                conn.execute(
                    select(table)
                    .where(timestamp_column >= window.start)
                    .where(timestamp_column < window.end)
                ).mappings()
            )
    except Exception:
        return []


def _position_event_rows(engine: Engine, window: ReportWindow) -> list[dict[str, Any]]:
    return _rows(engine, position_events, position_events.c.event_at, window)


def _details(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("details") or row.get("payload") or {}
    return value if isinstance(value, dict) else {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _account_section(position_rows: list[dict[str, Any]]) -> dict[str, Any]:
    snapshots = [_details(row) for row in position_rows if _details(row).get("account_equity") is not None]
    starting = _float(snapshots[0].get("account_equity"), 0.0) if snapshots else 0.0
    ending = _float(snapshots[-1].get("account_equity"), starting) if snapshots else starting
    realized = sum(_float(_details(row).get("realized_pnl")) for row in position_rows)
    unrealized_open = _float(snapshots[0].get("unrealized_pnl"), 0.0) if snapshots else 0.0
    unrealized_close = _float(snapshots[-1].get("unrealized_pnl"), unrealized_open) if snapshots else unrealized_open
    unrealized_delta = unrealized_close - unrealized_open
    total = realized + unrealized_delta
    return {
        "starting_equity": round(starting, 2),
        "ending_equity": round(ending, 2),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl_delta": round(unrealized_delta, 2),
        "total_pnl": round(total, 2),
        "net_pnl_pct": round((total / starting * 100) if starting else 0.0, 4),
    }


def _activity_section(position_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]]) -> dict[str, Any]:
    submitted = [row for row in position_rows if str(row.get("event_type")) == "submitted"]
    filled = [row for row in position_rows if str(row.get("event_type")) == "filled"]
    rejected = [row for row in position_rows if str(row.get("event_type")) == "broker_rejected"]
    open_submitted = [row for row in open_rows if str(row.get("verdict")) == "opened"]
    return {
        "orders_submitted": len(submitted) + len(open_submitted),
        "orders_filled": len(filled),
        "orders_rejected": len(rejected),
        "orders_by_type": {
            "open": len(open_submitted),
            "close": sum(1 for row in submitted if str(_details(row).get("action", "")).lower() == "close"),
            "rotate": 0,
        },
        "rejection_reasons": _counts([str(_details(row).get("reason") or "unknown") for row in rejected]),
        "average_slippage_bps": {
            "entry": round(_avg([_float(_details(row).get("slippage_bps")) for row in filled]), 4),
            "exit": 0.0,
            "round_trip": 0.0,
        },
        "total_trade_volume_usd": round(sum(_float(_details(row).get("notional") or _details(row).get("market_value")) for row in submitted), 2),
    }


def _counts(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda item: (-item[1], item[0])))


def _avg(values: list[float]) -> float:
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else 0.0


def _autopilot_section(position_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]], rotation_rows: list[dict[str, Any]], kill_rows: list[dict[str, Any]], eod_rows: list[dict[str, Any]]) -> dict[str, Any]:
    close_reasons = [str(_details(row).get("autopilot_reason") or _details(row).get("reason") or "unknown") for row in position_rows if str(row.get("source", "")).startswith("paper_autopilot")]
    return {
        "auto_closes": _counts(close_reasons),
        "auto_opens": {
            "count": sum(1 for row in open_rows if row.get("verdict") == "opened"),
            "value_usd": round(sum(_float(row.get("size_usd")) for row in open_rows if row.get("verdict") == "opened"), 2),
            "by_score_band": _score_bands(open_rows),
        },
        "auto_rotations": _counts([str(row.get("reason") or "unknown") for row in rotation_rows if row.get("verdict") == "confirmed"]),
        "operator_overrides": sum(1 for row in position_rows if row.get("event_type") == "operator_override") + sum(1 for row in rotation_rows if row.get("verdict") == "overridden"),
        "kill_switch_trips": _counts([str(row.get("switch_name")) for row in kill_rows if row.get("event_type") == "tripped"]),
        "eod_actions": _counts([str(row.get("state")) for row in eod_rows]),
    }


def _score_bands(open_rows: list[dict[str, Any]]) -> dict[str, int]:
    bands = {"0.65-0.75": 0, "0.75-0.85": 0, "0.85+": 0}
    for row in open_rows:
        if row.get("verdict") != "opened":
            continue
        score = _float(row.get("promotion_score"))
        if score >= 0.85:
            bands["0.85+"] += 1
        elif score >= 0.75:
            bands["0.75-0.85"] += 1
        else:
            bands["0.65-0.75"] += 1
    return bands


def _candidate_section(promotion_rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = _counts([str(row.get("verdict") or "unknown") for row in promotion_rows])
    blocks = _counts([str(row.get("block_reason") or "unknown") for row in promotion_rows if str(row.get("verdict")) == "block"])
    return {
        "total_candidates_evaluated": len(promotion_rows),
        "promotions_to_watch": verdicts.get("watch", 0),
        "promotions_to_selection": verdicts.get("promote_to_selection", 0),
        "promotions_to_selection_strong": verdicts.get("promote_to_selection_strong", 0),
        "blocks_by_reason_top5": dict(list(blocks.items())[:5]),
    }


def _best_worst_section(position_rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = []
    for row in position_rows:
        details = _details(row)
        if details.get("realized_pnl") is None:
            continue
        closed.append(
            {
                "symbol": details.get("symbol") or str(row.get("position_id", "")).split(":")[-1],
                "realized_pnl": round(_float(details.get("realized_pnl")), 2),
                "return_pct": round(_float(details.get("return_pct")), 4),
                "entry": _float(details.get("entry_price")),
                "exit": _float(details.get("exit_price")),
                "hold_time": details.get("hold_time", ""),
            }
        )
    ordered = sorted(closed, key=lambda row: row["realized_pnl"], reverse=True)
    return {
        "top_winners": [row for row in ordered if row["realized_pnl"] > 0][:3],
        "top_losers": sorted([row for row in closed if row["realized_pnl"] < 0], key=lambda row: row["realized_pnl"])[:3],
        "open_positions_at_close": [dict(row) for row in []],
    }


def _missed_opportunities(promotion_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    opened = {str(row.get("symbol")).upper() for row in open_rows if row.get("verdict") == "opened"}
    missed = []
    for row in promotion_rows:
        symbol = str(row.get("symbol") or "").upper()
        verdict = str(row.get("verdict") or "")
        block_reason = str(row.get("block_reason") or "")
        if verdict != "promote_to_selection_strong" or symbol in opened or block_reason in DEFENSIBLE_BLOCK_REASONS:
            continue
        missed.append(
            {
                "symbol": symbol,
                "promotion_score": round(_float(row.get("promotion_score")), 4),
                "reason": "strong_signal_not_acted",
                "price_2h_later": None,
            }
        )
    return missed


def _rule_triggers(promotion_rows: list[dict[str, Any]], rotation_rows: list[dict[str, Any]], kill_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]]) -> dict[str, int]:
    reasons = []
    reasons.extend(str(row.get("block_reason") or "promotion_block") for row in promotion_rows if row.get("verdict") == "block")
    reasons.extend(str(row.get("reason") or "rotation") for row in rotation_rows if row.get("verdict") in {"blocked", "expired"})
    reasons.extend(str(row.get("switch_name") or "kill_switch") for row in kill_rows if row.get("event_type") == "tripped")
    reasons.extend(str(row.get("block_reason") or "auto_open_block") for row in open_rows if row.get("verdict") in {"blocked", "failed"})
    return dict(list(_counts(reasons).items())[:10])


def _next_day_recommendations(missed: list[dict[str, Any]], kill_rows: list[dict[str, Any]], rotation_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    keys = []
    if missed:
        keys.append("MISSED_OPPORTUNITIES_REVIEW")
    if any(row.get("event_type") == "tripped" for row in kill_rows):
        keys.append("KILL_SWITCH_REVIEW")
    if rotation_rows:
        keys.append("ROTATION_REVIEW")
    if not keys:
        keys.append("NO_ACTION_REQUIRED")
    return [{"code": key, "text": RECOMMENDATION_TEMPLATES[key]} for key in keys]


def build_daily_report(session_date: date | str | None = None, *, engine: Engine | None = None, persist: bool = True, now: datetime | None = None) -> dict[str, Any]:
    day = date.fromisoformat(str(session_date)) if session_date is not None and not isinstance(session_date, date) else (session_date or utc_now().date())
    db = engine or get_engine(required=True)
    window = _window(day)
    position_rows = _position_event_rows(db, window)
    promotion_rows = _rows(db, intraday_promotion_log, intraday_promotion_log.c.logged_at, window)
    rotation_rows = _rows(db, rotation_recommendation_log, rotation_recommendation_log.c.logged_at, window)
    open_rows = _rows(db, autopilot_open_log, autopilot_open_log.c.logged_at, window)
    kill_rows = _rows(db, kill_switch_events, kill_switch_events.c.occurred_at, window)
    eod_rows = _rows(db, eod_flatten_log, eod_flatten_log.c.logged_at, window)
    eod_summary_rows = []
    try:
        with db.connect() as conn:
            eod_summary_rows = list(conn.execute(select(eod_summary).where(eod_summary.c.session_date == day)).mappings())
    except Exception:
        eod_summary_rows = []

    account = _account_section(position_rows)
    activity = _activity_section(position_rows, open_rows)
    autopilot = _autopilot_section(position_rows, open_rows, rotation_rows, kill_rows, eod_rows)
    candidates = _candidate_section(promotion_rows)
    best_worst = _best_worst_section(position_rows)
    missed = _missed_opportunities(promotion_rows, open_rows)
    should_have = [row for row in missed if row["reason"] == "strong_signal_not_acted"]
    rules = _rule_triggers(promotion_rows, rotation_rows, kill_rows, open_rows)
    recommendations = _next_day_recommendations(missed, kill_rows, rotation_rows)
    closed_trades = best_worst["top_winners"] + best_worst["top_losers"]
    total_trades = len({(row["symbol"], row["realized_pnl"]) for row in closed_trades})
    wins = sum(1 for row in closed_trades if row["realized_pnl"] > 0)
    win_rate = round(wins / total_trades * 100, 2) if total_trades else None
    computed_at = now or utc_now()
    payload = {
        "session_date": day.isoformat(),
        "computed_at": computed_at.isoformat(timespec="seconds"),
        "sections": {
            "account_state": account,
            "trading_activity": activity,
            "autopilot_actions": autopilot,
            "candidate_flow": candidates,
            "best_worst_trades": best_worst,
            "missed_opportunities": missed,
            "rule_triggers": rules,
            "should_have_done": should_have,
            "next_day_recommendations": recommendations,
            "eod_summary": [_jsonable(dict(row)) for row in eod_summary_rows],
        },
    }
    if persist:
        with db.begin() as conn:
            conn.execute(delete(daily_report_runs).where(daily_report_runs.c.session_date == day))
            conn.execute(
                insert(daily_report_runs).values(
                    session_date=day,
                    computed_at=computed_at,
                    starting_equity=account["starting_equity"],
                    ending_equity=account["ending_equity"],
                    realized_pnl=account["realized_pnl"],
                    unrealized_pnl_delta=account["unrealized_pnl_delta"],
                    total_pnl=account["total_pnl"],
                    net_pnl_pct=account["net_pnl_pct"],
                    win_rate=win_rate,
                    total_trades=total_trades,
                    details=payload,
                )
            )
    return payload


def report_index(*, engine: Engine | None = None, limit: int = 30) -> list[dict[str, Any]]:
    db = engine or get_engine(required=False)
    if db is None:
        return []
    with db.connect() as conn:
        rows = conn.execute(select(daily_report_runs).order_by(daily_report_runs.c.session_date.desc()).limit(limit)).mappings().all()
    return [_jsonable(dict(row)) for row in rows]


def get_or_build_report(session_date: date | str, *, engine: Engine | None = None, refresh: bool = False) -> dict[str, Any]:
    day = date.fromisoformat(str(session_date)) if not isinstance(session_date, date) else session_date
    db = engine or get_engine(required=False)
    if db is None:
        computed_at = utc_now().isoformat(timespec="seconds")
        return {
            "session_date": day.isoformat(),
            "computed_at": computed_at,
            "sections": {
                "account_state": _account_section([]),
                "trading_activity": _activity_section([], []),
                "autopilot_actions": _autopilot_section([], [], [], [], []),
                "candidate_flow": _candidate_section([]),
                "best_worst_trades": _best_worst_section([]),
                "missed_opportunities": [],
                "rule_triggers": {},
                "should_have_done": [],
                "next_day_recommendations": [{"code": "NO_ACTION_REQUIRED", "text": RECOMMENDATION_TEMPLATES["NO_ACTION_REQUIRED"]}],
                "eod_summary": [],
            },
        }
    if not refresh:
        with db.connect() as conn:
            row = conn.execute(select(daily_report_runs.c.details).where(daily_report_runs.c.session_date == day)).scalar()
            if isinstance(row, dict):
                return row
    return build_daily_report(day, engine=db, persist=True)


def report_csv(report: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["section", "metric", "value"])
    for section, payload in (report.get("sections") or {}).items():
        if isinstance(payload, dict):
            for key, value in payload.items():
                writer.writerow([section, key, value])
        elif isinstance(payload, list):
            writer.writerow([section, "count", len(payload)])
    return out.getvalue()
