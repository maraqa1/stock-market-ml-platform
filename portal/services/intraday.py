from __future__ import annotations

from datetime import datetime
import csv
import io
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import intraday_decisions, shadow_outcomes, shadow_would_trades
from stockml.intraday import kill_switch


def _connect(target: Engine | Connection | None = None):
    if isinstance(target, Connection):
        return target, None
    engine = target or get_engine(required=False)
    if engine is None:
        return None, None
    context = engine.connect()
    return context.__enter__(), context


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value


def _serializable(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _iso(value) for key, value in row.items()}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _clean_event(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    value = out.get("occurred_at")
    if isinstance(value, datetime):
        out["occurred_at"] = value.isoformat(timespec="seconds")
    return out


def kill_switch_context() -> dict[str, Any]:
    payload = kill_switch.state()
    switches = []
    for row in payload.get("switches", []):
        status = str(row.get("status") or "armed")
        switches.append(
            {
                **row,
                "label": str(row.get("name", "")).replace(".", " / ").replace("_", " ").title(),
                "status_label": "Tripped" if status == "tripped" else "Armed",
                "pill_status": "failed" if status == "tripped" else "safe",
                "requires_manual_resume": status == "tripped",
            }
        )
    events = [_clean_event(dict(row)) for row in payload.get("events", [])]
    return {
        **payload,
        "switches": switches,
        "events": list(reversed(events[-20:])),
        "tripped_count": len(payload.get("active", [])),
        "armed_count": max(0, len(switches) - len(payload.get("active", []))),
    }


def resume_kill_switch(switch_name: str, operator_id: str, notes: str) -> None:
    kill_switch.resume(switch_name, operator_id, notes)


def intraday_filters(args) -> dict[str, Any]:
    return {
        "verdict": [value for value in args.getlist("verdict") if value],
        "block_reason": [value for value in args.getlist("block_reason") if value],
        "symbol": str(args.get("symbol", "") or "").strip().upper(),
        "limit": min(max(_int(args.get("limit", 100)), 1), 500),
    }


def decisions_payload(filters: dict[str, Any] | None = None, target: Engine | Connection | None = None) -> dict[str, Any]:
    filters = filters or {}
    conn, context = _connect(target)
    if conn is None:
        return {"rows": [], "summary": {"ticks_today": 0, "decisions": 0, "would_trades": 0, "blocks": 0}, "block_histogram": []}
    try:
        query = select(intraday_decisions).order_by(desc(intraday_decisions.c.decided_at)).limit(_int(filters.get("limit")) or 100)
        if filters.get("verdict"):
            query = query.where(intraday_decisions.c.verdict.in_(filters["verdict"]))
        if filters.get("block_reason"):
            query = query.where(intraday_decisions.c.block_reason.in_(filters["block_reason"]))
        if filters.get("symbol"):
            query = query.where(intraday_decisions.c.symbol == filters["symbol"])
        rows = [_serializable(dict(row)) for row in conn.execute(query).mappings().all()]

        decisions = conn.execute(select(func.count()).select_from(intraday_decisions)).scalar() or 0
        would_trades = conn.execute(select(func.count()).select_from(shadow_would_trades)).scalar() or 0
        blocks = conn.execute(select(func.count()).select_from(intraday_decisions).where(intraday_decisions.c.verdict == "block")).scalar() or 0
        tick_count = conn.execute(select(func.count(func.distinct(intraday_decisions.c.bar_close_at)))).scalar() or 0
        histogram_rows = conn.execute(
            select(intraday_decisions.c.block_reason, func.count().label("count"))
            .where(intraday_decisions.c.block_reason.is_not(None))
            .group_by(intraday_decisions.c.block_reason)
            .order_by(desc(func.count()))
            .limit(5)
        ).all()
        return {
            "rows": rows,
            "summary": {
                "ticks_today": int(tick_count),
                "decisions": int(decisions),
                "would_trades": int(would_trades),
                "blocks": int(blocks),
            },
            "block_histogram": [{"reason": str(reason), "count": int(count)} for reason, count in histogram_rows],
        }
    except Exception:
        return {"rows": [], "summary": {"ticks_today": 0, "decisions": 0, "would_trades": 0, "blocks": 0}, "block_histogram": []}
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def shadow_track_record(target: Engine | Connection | None = None, limit: int = 30) -> dict[str, Any]:
    conn, context = _connect(target)
    if conn is None:
        return {"rows": [], "summary": {"n_evaluated": 0, "mean_net_excess_pct": None, "hit_rate": None, "max_concentration_pct": None}}
    try:
        joined = shadow_would_trades.join(shadow_outcomes, shadow_would_trades.c.id == shadow_outcomes.c.would_trade_id)
        rows = [
            _serializable(dict(row))
            for row in conn.execute(
                select(
                    shadow_would_trades.c.symbol,
                    shadow_would_trades.c.side,
                    shadow_would_trades.c.decided_at,
                    shadow_would_trades.c.evaluation_date,
                    shadow_outcomes.c.raw_return_pct,
                    shadow_outcomes.c.cost_bps,
                    shadow_outcomes.c.net_return_pct,
                    shadow_outcomes.c.spy_return_pct,
                    shadow_outcomes.c.net_excess_pct,
                    shadow_outcomes.c.outperformed,
                )
                .select_from(joined)
                .order_by(desc(shadow_outcomes.c.evaluated_at))
                .limit(limit)
            ).mappings().all()
        ]
        all_rows = conn.execute(select(shadow_would_trades.c.symbol, shadow_outcomes.c.net_excess_pct, shadow_outcomes.c.outperformed).select_from(joined)).mappings().all()
        n = len(all_rows)
        if n:
            mean_excess = sum(float(row["net_excess_pct"] or 0) for row in all_rows) / n
            hit_rate = sum(1 for row in all_rows if row["outperformed"]) / n
            total_abs = sum(abs(float(row["net_excess_pct"] or 0)) for row in all_rows)
            by_symbol: dict[str, float] = {}
            for row in all_rows:
                by_symbol[row["symbol"]] = by_symbol.get(row["symbol"], 0.0) + abs(float(row["net_excess_pct"] or 0))
            max_concentration = max(by_symbol.values()) / total_abs if total_abs else 0.0
        else:
            mean_excess = hit_rate = max_concentration = None
        return {
            "rows": rows,
            "summary": {
                "n_evaluated": n,
                "mean_net_excess_pct": mean_excess,
                "hit_rate": hit_rate,
                "max_concentration_pct": max_concentration,
            },
        }
    except Exception:
        return {"rows": [], "summary": {"n_evaluated": 0, "mean_net_excess_pct": None, "hit_rate": None, "max_concentration_pct": None}}
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def promotion_readiness_placeholder() -> dict[str, Any]:
    return {
        "criteria": [
            {"name": "Sufficient samples", "status": "pending", "note": "SPEC 36 will evaluate long and short sample counts."},
            {"name": "Positive net excess", "status": "pending", "note": "SPEC 36 will compare shadow against nightly-only baseline."},
            {"name": "Calibration holds", "status": "pending", "note": "SPEC 36 will validate top-bucket hit rate."},
            {"name": "Low concentration", "status": "pending", "note": "SPEC 36 will check symbol and day concentration."},
            {"name": "Operator dry run", "status": "pending", "note": "SPEC 36 will require manual paper confirmations."},
        ],
        "status_text": "Promotion criteria not evaluated yet. Live trading remains disabled.",
    }


def intraday_context(args=None, target: Engine | Connection | None = None) -> dict[str, Any]:
    filters = intraday_filters(args) if args is not None else {"verdict": [], "block_reason": [], "symbol": "", "limit": 100}
    return {
        "filters": filters,
        "flow": decisions_payload(filters, target),
        "track_record": shadow_track_record(target),
        "kill_switches": kill_switch_context(),
        "promotion": promotion_readiness_placeholder(),
    }


def decisions_csv(filters: dict[str, Any] | None = None, target: Engine | Connection | None = None) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "decided_at", "symbol", "verdict", "block_reason", "valid_until", "gate_version"])
    writer.writeheader()
    for row in decisions_payload(filters, target)["rows"]:
        writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    return output.getvalue()
