from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import intraday_features, same_day_candidates, same_day_signal_log
from stockml.same_day import gates
from stockml.same_day.scoring import SameDayModelBundle, load_model_bundle, score_features


def _aware(value: datetime | None = None) -> datetime:
    stamp = value or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def load_features_for_tick(decision_time: datetime, *, engine: Engine | None = None) -> list[dict[str, Any]]:
    db = engine or get_engine(required=True)
    with db.connect() as conn:
        rows = conn.execute(
            select(intraday_features).where(intraday_features.c.decision_time == _aware(decision_time)).order_by(intraday_features.c.symbol.asc())
        ).mappings()
        return [dict(row) for row in rows]


def _count_candidates_today(conn, stamp: datetime) -> int:
    day = _aware(stamp).date()
    return int(
        conn.execute(
            select(func.count()).select_from(same_day_candidates).where(func.date(same_day_candidates.c.generated_at) == str(day))
        ).scalar_one()
        or 0
    )


def _count_symbol_attempts_today(conn, symbol: str, stamp: datetime) -> int:
    day = _aware(stamp).date()
    return int(
        conn.execute(
            select(func.count())
            .select_from(same_day_signal_log)
            .where(same_day_signal_log.c.symbol == symbol.upper())
            .where(func.date(same_day_signal_log.c.logged_at) == str(day))
        ).scalar_one()
        or 0
    )


def _candidate_exists(conn, symbol: str, decision: datetime) -> bool:
    return bool(
        conn.execute(
            select(func.count())
            .select_from(same_day_candidates)
            .where(same_day_candidates.c.symbol == symbol.upper())
            .where(same_day_candidates.c.decision_time == decision)
        ).scalar_one()
    )


def score_tick(
    *,
    decision_time: datetime,
    engine: Engine | None = None,
    model_loader: Callable[[], SameDayModelBundle] = load_model_bundle,
    gate_evaluator: Callable[..., gates.GateResult] = gates.evaluate,
    now: datetime | None = None,
) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    decision = _aware(decision_time)
    bundle = model_loader()
    feature_rows = load_features_for_tick(decision, engine=db)
    signal_rows = 0
    candidate_rows = 0
    with db.begin() as conn:
        for row in feature_rows:
            if str(row.get("status") or "") != "ok":
                continue
            symbol = str(row["symbol"]).upper()
            features = dict(row.get("features") or {})
            score = score_features(features, bundle)
            attempts = _count_symbol_attempts_today(conn, symbol, stamp)
            daily_count = _count_candidates_today(conn, stamp)
            gate_result = gate_evaluator(
                features,
                direction=score.direction,
                continuation_probability=score.continuation_probability,
                reversal_probability=score.reversal_probability,
                same_day_attempts_today_for_symbol=attempts,
                same_day_candidates_today_count=daily_count,
                engine=db,
                now=stamp,
            )
            gate_outcome = "passed" if gate_result.passed else f"blocked:{gate_result.reason}"
            conn.execute(
                insert(same_day_signal_log).values(
                    logged_at=stamp,
                    decision_time=decision,
                    symbol=symbol,
                    direction=score.direction,
                    continuation_probability=score.continuation_probability,
                    reversal_probability=score.reversal_probability,
                    gate_outcome=gate_outcome,
                    block_reason=None if gate_result.passed else gate_result.reason,
                    features_id=row["id"],
                )
            )
            signal_rows += 1
            if gate_result.passed and not _candidate_exists(conn, symbol, decision):
                conn.execute(
                    insert(same_day_candidates).values(
                        generated_at=stamp,
                        decision_time=decision,
                        symbol=symbol,
                        direction=score.direction,
                        continuation_probability=score.continuation_probability,
                        reversal_probability=score.reversal_probability,
                        model_id=bundle.model_id,
                        features_id=row["id"],
                        same_day_confidence=score.same_day_confidence,
                        same_day_reason=f"{score.direction}_continuation_probability:{score.continuation_probability:.4f}",
                        strategy_stream="same_day_momentum",
                        max_hold_days=1,
                        must_flatten_eod=True,
                        arbitration_outcome=None,
                    )
                )
                candidate_rows += 1
    return {
        "status": "ok",
        "features_seen": len(feature_rows),
        "signals_logged": signal_rows,
        "candidates_emitted": candidate_rows,
        "model_id": bundle.model_id,
    }
