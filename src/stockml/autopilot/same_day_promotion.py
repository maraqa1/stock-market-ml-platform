from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import same_day_candidates, same_day_promotion_evaluations


@dataclass(frozen=True)
class Criterion:
    name: str
    met: bool
    observed: Any
    threshold: Any
    note: str = ""


def _now(value: datetime | None = None) -> datetime:
    stamp = value or datetime.now(timezone.utc)
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _trade_rows(engine: Engine, now: datetime) -> list[dict[str, Any]]:
    start = now - timedelta(days=30)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(same_day_candidates)
                .where(same_day_candidates.c.strategy_stream == "same_day_momentum")
                .where(same_day_candidates.c.arbitration_outcome.in_(("confirmed", "paper_assist_opened")))
                .where(same_day_candidates.c.generated_at >= start)
            ).mappings().all()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return default


def _pnl(row: dict[str, Any]) -> float:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    return _num(details.get("realized_net_pnl") or details.get("realized_pnl") or row.get("realized_net_pnl"))


def _bps(row: dict[str, Any], key: str) -> float:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    return _num(details.get(key) or row.get(key))


def evaluate_contract(trades: list[dict[str, Any]], *, candidates_presented: int | None = None, kill_switch_cascade_days: int = 0) -> dict[str, Any]:
    sample_count = len(trades)
    dates = {str(row.get("generated_at", ""))[:10] for row in trades if row.get("generated_at")}
    pnls = [_pnl(row) for row in trades]
    avg = mean(pnls) if pnls else 0.0
    std = pstdev(pnls) if len(pnls) > 1 else 0.0
    t_stat = avg / (std / (len(pnls) ** 0.5)) if std else (99.0 if avg > 0 else 0.0)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [abs(pnl) for pnl in pnls if pnl < 0]
    hit_rate = len(wins) / sample_count if sample_count else 0.0
    rr_ratio = (mean(wins) / mean(losses)) if wins and losses else (99.0 if wins else 0.0)
    presented = candidates_presented if candidates_presented is not None else sample_count
    override_rate = 1.0 - (sample_count / max(1, presented))
    worst_day = min(_daily_pnl(trades).values(), default=0.0)
    total = sum(pnls)
    symbol_concentration = _max_group_concentration(trades, "symbol", pnls, total)
    day_concentration = _max_group_concentration(trades, "generated_at", pnls, total, date_prefix=True)
    criteria = [
        Criterion("SUFFICIENT_SAMPLES", sample_count >= 100 and len(dates) >= 30, f"{sample_count} trades / {len(dates)} days", ">=100 trades over >=30 days"),
        Criterion("POSITIVE_NET_PNL", avg > 0 and t_stat > 2, {"mean": round(avg, 4), "t_stat": round(t_stat, 4)}, "mean > 0 and t-stat > 2"),
        Criterion("HIT_RATE", hit_rate >= 0.50, round(hit_rate, 4), ">= 0.50"),
        Criterion("RR_RATIO", rr_ratio >= 1.5, round(rr_ratio, 4), ">= 1.5"),
        Criterion("OVERRIDE_RATE", override_rate < 0.25, round(override_rate, 4), "< 0.25"),
        Criterion("WORST_DAY", worst_day > -0.02, round(worst_day, 4), "> -2% equity"),
        Criterion("CONCENTRATION", symbol_concentration <= 0.30 and day_concentration <= 0.25, {"symbol": round(symbol_concentration, 4), "day": round(day_concentration, 4)}, "symbol <=30%, day <=25%"),
        Criterion("NO_KILL_SWITCH_CASCADES", kill_switch_cascade_days == 0, kill_switch_cascade_days, "0 days"),
    ]
    rows = [criterion.__dict__ for criterion in criteria]
    return {"criteria_met": all(row["met"] for row in rows), "criteria_results": rows}


def _daily_pnl(trades: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in trades:
        day = str(row.get("generated_at", ""))[:10]
        out[day] = out.get(day, 0.0) + _pnl(row)
    return out


def _max_group_concentration(trades: list[dict[str, Any]], key: str, pnls: list[float], total: float, *, date_prefix: bool = False) -> float:
    if not trades or total <= 0:
        return 0.0
    groups: dict[str, float] = {}
    for row, pnl in zip(trades, pnls):
        group = str(row.get(key) or "")
        if date_prefix:
            group = group[:10]
        groups[group] = groups.get(group, 0.0) + pnl
    return max((value / total for value in groups.values()), default=0.0)


def evaluate_and_record(*, engine: Engine | None = None, now: datetime | None = None, activated: bool = False) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    stamp = _now(now)
    result = evaluate_contract(_trade_rows(db, stamp))
    with db.begin() as conn:
        conn.execute(
            insert(same_day_promotion_evaluations).values(
                evaluated_at=stamp,
                criteria_met=bool(result["criteria_met"]),
                criteria_results=result["criteria_results"],
                activated=bool(activated and result["criteria_met"]),
            )
        )
    return {"evaluated_at": stamp.isoformat(), **result, "activated": bool(activated and result["criteria_met"])}


def latest_evaluation(*, engine: Engine | None = None) -> dict[str, Any]:
    db = engine or get_engine(required=False)
    if db is None:
        result = evaluate_contract([])
        return {"evaluated_at": "", **result, "activated": False}
    try:
        with db.connect() as conn:
            row = conn.execute(select(same_day_promotion_evaluations).order_by(same_day_promotion_evaluations.c.evaluated_at.desc()).limit(1)).mappings().first()
        if row:
            return {
                "evaluated_at": row["evaluated_at"].isoformat() if hasattr(row["evaluated_at"], "isoformat") else str(row["evaluated_at"]),
                "criteria_met": bool(row["criteria_met"]),
                "criteria_results": row["criteria_results"],
                "activated": bool(row["activated"]),
            }
    except Exception:
        pass
    result = evaluate_contract([])
    return {"evaluated_at": "", **result, "activated": False}
