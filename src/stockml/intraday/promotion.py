from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, NamedTuple

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import promotion_dry_runs, promotion_evaluations, shadow_outcomes, shadow_would_trades


GATE_VERSION = "promotion-v1.0.0"


class CriterionResult(NamedTuple):
    name: str
    met: bool
    observed: float | str
    threshold: float | str
    note: str


def _fallback_evaluation(note: str = "promotion tables are not available; live trading remains disabled") -> dict[str, Any]:
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_version": GATE_VERSION,
        "criteria_met": False,
        "criteria_results": [
            _as_dict(CriterionResult("PROMOTION_STORAGE_READY", False, "unavailable", "available", note)),
        ],
        "notes": note,
    }


def _connect(target: Engine | Connection | None = None):
    if isinstance(target, Connection):
        return target, None
    engine = target or get_engine(required=False)
    if engine is None:
        return None, None
    context = engine.connect()
    return context.__enter__(), context


def _begin(target: Engine | Connection | None = None):
    if isinstance(target, Connection):
        return target, None
    engine = target or get_engine(required=False)
    if engine is None:
        return None, None
    context = engine.begin()
    return context.__enter__(), context


def _as_dict(result: CriterionResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "met": result.met,
        "observed": result.observed,
        "threshold": result.threshold,
        "note": result.note,
    }


def _window_rows(conn: Connection, from_date: date, to_date: date) -> list[dict[str, Any]]:
    joined = shadow_would_trades.join(shadow_outcomes, shadow_would_trades.c.id == shadow_outcomes.c.would_trade_id)
    rows = conn.execute(
        select(
            shadow_would_trades.c.symbol,
            shadow_would_trades.c.side,
            shadow_would_trades.c.decided_at,
            shadow_would_trades.c.nightly_score,
            shadow_outcomes.c.net_excess_pct,
            shadow_outcomes.c.outperformed,
        )
        .select_from(joined)
        .where(shadow_would_trades.c.evaluation_date >= from_date)
        .where(shadow_would_trades.c.evaluation_date <= to_date)
    ).mappings().all()
    return [dict(row) for row in rows]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_criteria(
    *,
    from_date: date,
    to_date: date,
    target: Engine | Connection | None = None,
    expected_baseline_excess_pct: float = 0.0,
) -> list[CriterionResult]:
    conn, context = _connect(target)
    if conn is None:
        return []
    try:
        rows = _window_rows(conn, from_date, to_date)
        long_count = sum(1 for row in rows if row["side"] == "long")
        short_count = sum(1 for row in rows if row["side"] == "short")
        window_days = (to_date - from_date).days + 1
        net_excess = [float(row["net_excess_pct"] or 0) for row in rows]
        mean_excess = _mean(net_excess)
        top_bucket = [row for row in rows if float(row.get("nightly_score") or 0) >= 0.7]
        top_hit_rate = sum(1 for row in top_bucket if row["outperformed"]) / len(top_bucket) if top_bucket else 0.0
        total_abs = sum(abs(value) for value in net_excess)
        by_symbol: dict[str, float] = {}
        by_day: dict[date, float] = {}
        for row in rows:
            value = abs(float(row["net_excess_pct"] or 0))
            by_symbol[row["symbol"]] = by_symbol.get(row["symbol"], 0.0) + value
            day = row["decided_at"].date()
            by_day[day] = by_day.get(day, 0.0) + value
        max_symbol_concentration = max(by_symbol.values()) / total_abs if total_abs else 1.0
        max_day_concentration = max(by_day.values()) / total_abs if total_abs else 1.0
        dry_cutoff = datetime.combine(to_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=14)
        dry_rows = conn.execute(select(promotion_dry_runs).where(promotion_dry_runs.c.confirmed_at >= dry_cutoff)).mappings().all()
        dry_count = len(dry_rows)
        dry_ops = {str(row["operator_id"]) for row in dry_rows}
        dry_operator_ok = len(dry_ops) >= 3 if len(dry_ops) > 1 else dry_count >= 5
        return [
            CriterionResult("SUFFICIENT_SAMPLES", long_count >= 200 and short_count >= 200 and window_days >= 60, f"long={long_count}, short={short_count}, days={window_days}", "long>=200, short>=200, days>=60", "Requires both sides and a 60-day window."),
            CriterionResult("POSITIVE_NET_EXCESS", mean_excess > expected_baseline_excess_pct, mean_excess, f">{expected_baseline_excess_pct}", "Shadow net excess must beat the nightly-only baseline."),
            CriterionResult("CALIBRATION_HOLDS", bool(top_bucket) and abs(top_hit_rate - 0.70) <= 0.05, top_hit_rate, "0.65..0.75", "Top-bucket realized hit rate must stay close to expected."),
            CriterionResult("LOW_CONCENTRATION_BY_SYMBOL", max_symbol_concentration < 0.25, max_symbol_concentration, "<0.25", "No single symbol may dominate total excess."),
            CriterionResult("LOW_CONCENTRATION_BY_DAY", max_day_concentration < 0.20, max_day_concentration, "<0.20", "No single day may dominate total excess."),
            CriterionResult("ABLATION_LIFT", mean_excess - expected_baseline_excess_pct > 0.003, mean_excess - expected_baseline_excess_pct, ">0.003", "Intraday confirmation must lift nightly-only excess by more than 0.3 pp."),
            CriterionResult("OPERATOR_DRY_RUN", dry_count >= 5 and dry_operator_ok, f"confirmations={dry_count}, operators={len(dry_ops)}", ">=5 confirmations", "Operator paper confirmations require notes and recent manual review."),
        ]
    except Exception:
        return []
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def evaluate_promotion(
    *,
    as_of: datetime | None = None,
    window_days: int = 90,
    target: Engine | Connection | None = None,
) -> dict[str, Any]:
    stamp = as_of or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    to_date = stamp.date()
    from_date = to_date - timedelta(days=window_days - 1)
    results = evaluate_criteria(from_date=from_date, to_date=to_date, target=target)
    payload = [_as_dict(result) for result in results]
    met = bool(results) and all(result.met for result in results)
    conn, context = _begin(target)
    if conn is not None:
        try:
            conn.execute(delete(promotion_evaluations).where(func.date(promotion_evaluations.c.evaluated_at) == to_date))
            conn.execute(
                insert(promotion_evaluations).values(
                    evaluated_at=stamp,
                    gate_version=GATE_VERSION,
                    criteria_met=met,
                    criteria_results=payload,
                    notes="live trading remains disabled",
                )
            )
        except Exception:
            return {"evaluated_at": stamp.isoformat(timespec="seconds"), "gate_version": GATE_VERSION, "criteria_met": False, "criteria_results": payload, "notes": "promotion storage unavailable; live trading remains disabled"}
        finally:
            if context is not None:
                context.__exit__(None, None, None)
    return {"evaluated_at": stamp.isoformat(timespec="seconds"), "gate_version": GATE_VERSION, "criteria_met": met, "criteria_results": payload, "notes": "live trading remains disabled"}


def latest_evaluation(target: Engine | Connection | None = None) -> dict[str, Any]:
    conn, context = _connect(target)
    if conn is None:
        return evaluate_promotion(target=target)
    try:
        row = conn.execute(select(promotion_evaluations).order_by(promotion_evaluations.c.evaluated_at.desc()).limit(1)).mappings().first()
        if row:
            return {**dict(row), "evaluated_at": row["evaluated_at"].isoformat(timespec="seconds")}
    except Exception:
        return _fallback_evaluation()
    finally:
        if context is not None:
            context.__exit__(None, None, None)
    return evaluate_promotion(target=target)


def record_operator_dry_run_confirmation(
    *,
    operator_id: str,
    symbol: str,
    side: str,
    notes: str,
    confirmed_at: datetime | None = None,
    target: Engine | Connection | None = None,
) -> int | None:
    if not notes.strip():
        raise ValueError("operator dry-run confirmation requires notes")
    side_clean = side.lower()
    if side_clean not in {"long", "short"}:
        raise ValueError("side must be long or short")
    stamp = confirmed_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    conn, context = _begin(target)
    if conn is None:
        return None
    try:
        result = conn.execute(
            insert(promotion_dry_runs).values(
                confirmed_at=stamp,
                operator_id=operator_id,
                symbol=symbol.upper(),
                side=side_clean,
                notes=notes,
            )
        )
        return result.inserted_primary_key[0] if result.inserted_primary_key else None
    finally:
        if context is not None:
            context.__exit__(None, None, None)
