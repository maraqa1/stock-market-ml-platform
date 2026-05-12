from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yaml
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from stockml.common.paths import PROJECT_ROOT
from stockml.db.connection import get_engine
from stockml.db.schema import rotation_recommendation_log
from stockml.intraday import kill_switch
from stockml.services.events import position_id_for_symbol, record_event_safely


MONITOR_CONFIG_PATH = PROJECT_ROOT / "config" / "monitor.yaml"


class RotationReason(str, Enum):
    HIGHER_PROMOTION_SCORE = "HIGHER_PROMOTION_SCORE"
    HELD_SIGNAL_STALE = "HELD_SIGNAL_STALE"
    HELD_NEGATIVE_TREND = "HELD_NEGATIVE_TREND"
    HELD_DROPPED_FROM_SHORTLIST = "HELD_DROPPED_FROM_SHORTLIST"


@dataclass(frozen=True)
class RotationConfig:
    enabled: bool = True
    min_score_delta: float = 0.10
    min_hold_minutes: int = 60
    max_rotations_per_day: int = 3
    require_operator_confirm: bool = True


@dataclass(frozen=True)
class Rotation:
    replace_symbol: str
    with_symbol: str
    replace_position_id: str
    promotion_score: float
    held_score: float
    score_delta: float
    reason: RotationReason
    details: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None = None) -> datetime:
    out = value or _now()
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        parsed = float(value)
        if pd.isna(parsed):
            return default
        return parsed
    except Exception:
        return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _bias(value: Any) -> str:
    text = _text(value).lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    return text


def load_rotation_config(path: Path | str = MONITOR_CONFIG_PATH) -> RotationConfig:
    payload: dict[str, Any] = {}
    config_path = Path(path)
    if config_path.exists():
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    rotation = payload.get("rotation") if isinstance(payload, dict) else {}
    rotation = rotation if isinstance(rotation, dict) else {}
    return RotationConfig(
        enabled=bool(rotation.get("enabled", True)),
        min_score_delta=float(rotation.get("promotion_min_score_delta", rotation.get("min_score_delta", 0.10))),
        min_hold_minutes=int(rotation.get("min_hold_minutes", 60)),
        max_rotations_per_day=int(rotation.get("max_rotations_per_day", 3)),
        require_operator_confirm=bool(rotation.get("require_operator_confirm", True)),
    )


def _position_open_minutes(row: dict[str, Any], now: datetime) -> float:
    opened = None
    for key in ["opened_at", "submitted_at", "updated_at", "filled_at"]:
        opened = _time(row.get(key))
        if opened:
            break
    if opened is None:
        return 10_000.0
    return max(0.0, (now - _aware(opened)).total_seconds() / 60)


def _same_side(candidate: dict[str, Any], position: dict[str, Any]) -> bool:
    candidate_bias = _bias(candidate.get("nightly_bias") or candidate.get("bias") or candidate.get("side"))
    position_bias = _bias(position.get("side") or position.get("bias"))
    if not candidate_bias or not position_bias:
        return True
    return candidate_bias == position_bias


def _held_symbols(open_positions: list[dict[str, Any]]) -> set[str]:
    return {_text(row.get("symbol")).upper() for row in open_positions if _text(row.get("symbol"))}


def _position_score(position: dict[str, Any]) -> float:
    for key in ["last_promotion_score", "promotion_score", "score", "nightly_score"]:
        if key in position:
            return _float(position.get(key))
    return 0.0


def _derive_reason(candidate: dict[str, Any], position: dict[str, Any]) -> RotationReason:
    reasons = str(position.get("decision_reason") or "").lower()
    trend = _float(position.get("trend_5m_pct"))
    if "dropped_from_shortlist" in reasons:
        return RotationReason.HELD_DROPPED_FROM_SHORTLIST
    if "signal_stale" in reasons:
        return RotationReason.HELD_SIGNAL_STALE
    if trend < 0:
        return RotationReason.HELD_NEGATIVE_TREND
    return RotationReason.HIGHER_PROMOTION_SCORE


def _daily_rotation_count(engine: Engine | None, now: datetime) -> int:
    if engine is None:
        return 0
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count(rotation_recommendation_log.c.id))
                .where(rotation_recommendation_log.c.logged_at >= start)
                .where(rotation_recommendation_log.c.verdict.in_(("proposed", "confirmed")))
            ).scalar()
            or 0
        )


def _kill_switch_allows(gate: Callable[..., kill_switch.KillSwitchVerdict], engine: Engine | None, now: datetime) -> bool:
    verdict = gate(action="decide", engine=engine, now=now)
    return bool(verdict.allow)


def find_weaker_held(candidate: dict[str, Any], open_positions: list[dict[str, Any]], config: RotationConfig, now: datetime) -> dict[str, Any] | None:
    candidate_score = _float(candidate.get("promotion_score"))
    candidates: list[tuple[float, dict[str, Any]]] = []
    for position in open_positions:
        if not _same_side(candidate, position):
            continue
        if _position_open_minutes(position, now) < config.min_hold_minutes:
            continue
        pnl_pct = _float(position.get("unrealized_plpc"))
        if pnl_pct >= 0.03:
            continue
        held_score = _position_score(position)
        delta = candidate_score - held_score
        if delta >= config.min_score_delta:
            candidates.append((held_score, position))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def evaluate_rotations(
    promoted: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    *,
    config: RotationConfig | None = None,
    engine: Engine | None = None,
    now: datetime | None = None,
    kill_switch_gate: Callable[..., kill_switch.KillSwitchVerdict] = kill_switch.gate,
) -> list[Rotation]:
    stamp = _aware(now)
    cfg = config or load_rotation_config()
    if not cfg.enabled or not _kill_switch_allows(kill_switch_gate, engine, stamp):
        return []
    if _daily_rotation_count(engine, stamp) >= cfg.max_rotations_per_day:
        return []
    held = _held_symbols(open_positions)
    out: list[Rotation] = []
    for candidate in promoted:
        symbol = _text(candidate.get("symbol")).upper()
        if not symbol or symbol in held:
            continue
        weaker = find_weaker_held(candidate, open_positions, cfg, stamp)
        if weaker is None:
            continue
        replace = _text(weaker.get("symbol")).upper()
        promotion_score = _float(candidate.get("promotion_score"))
        held_score = _position_score(weaker)
        out.append(
            Rotation(
                replace_symbol=replace,
                with_symbol=symbol,
                replace_position_id=str(weaker.get("position_id") or position_id_for_symbol(replace)),
                promotion_score=promotion_score,
                held_score=held_score,
                score_delta=promotion_score - held_score,
                reason=_derive_reason(candidate, weaker),
                details={"require_operator_confirm": cfg.require_operator_confirm},
            )
        )
    return out[: max(0, cfg.max_rotations_per_day)]


def latest_promoted_candidates(*, engine: Engine | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from stockml.db.schema import intraday_candidate_snapshots, intraday_promotion_log

    db = engine or get_engine(required=True)
    with db.connect() as conn:
        latest_tick = conn.execute(select(func.max(intraday_candidate_snapshots.c.snapshot_at))).scalar()
        if latest_tick is None:
            return []
        joined = intraday_promotion_log.join(
            intraday_candidate_snapshots,
            intraday_promotion_log.c.snapshot_id == intraday_candidate_snapshots.c.id,
        )
        rows = conn.execute(
            select(
                intraday_promotion_log.c.symbol,
                intraday_promotion_log.c.promotion_score,
                intraday_promotion_log.c.verdict,
                intraday_candidate_snapshots.c.nightly_bias,
                intraday_candidate_snapshots.c.is_held,
            )
            .select_from(joined)
            .where(intraday_candidate_snapshots.c.snapshot_at == latest_tick)
            .where(intraday_promotion_log.c.verdict.in_(("promote_to_selection", "promote_to_selection_strong")))
            .order_by(intraday_promotion_log.c.promotion_score.desc())
            .limit(limit)
        ).mappings().all()
    return [dict(row) for row in rows]


def write_rotation_recommendations(
    promoted: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
    kill_switch_gate: Callable[..., kill_switch.KillSwitchVerdict] = kill_switch.gate,
) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    rotations = evaluate_rotations(promoted, open_positions, engine=db, now=now, kill_switch_gate=kill_switch_gate)
    written = 0
    for rotation in rotations:
        if record_rotation(rotation, engine=db, now=now) is not None:
            written += 1
    return {"status": "ok", "rotations_evaluated": len(rotations), "rotations_written": written}


def record_rotation(rotation: Rotation, *, engine: Engine | None = None, now: datetime | None = None, verdict: str = "proposed") -> int | None:
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    with db.begin() as conn:
        existing = conn.execute(
            select(rotation_recommendation_log.c.id)
            .where(rotation_recommendation_log.c.replace_symbol == rotation.replace_symbol)
            .where(rotation_recommendation_log.c.with_symbol == rotation.with_symbol)
            .where(rotation_recommendation_log.c.verdict == "proposed")
            .limit(1)
        ).first()
        if existing:
            return existing[0]
        result = conn.execute(
            insert(rotation_recommendation_log).values(
                logged_at=stamp,
                replace_symbol=rotation.replace_symbol,
                with_symbol=rotation.with_symbol,
                replace_position_id=rotation.replace_position_id,
                promotion_score=rotation.promotion_score,
                held_score=rotation.held_score,
                score_delta=rotation.score_delta,
                reason=rotation.reason.value,
                verdict=verdict,
                details=rotation.details,
            )
        )
        return result.inserted_primary_key[0] if result.inserted_primary_key else None


def expire_old_rotations(*, engine: Engine | None = None, now: datetime | None = None, max_age_minutes: int = 30) -> int:
    db = engine or get_engine(required=True)
    cutoff = _aware(now) - timedelta(minutes=max_age_minutes)
    with db.begin() as conn:
        result = conn.execute(
            update(rotation_recommendation_log)
            .where(rotation_recommendation_log.c.verdict == "proposed")
            .where(rotation_recommendation_log.c.logged_at < cutoff)
            .values(verdict="expired")
        )
    return int(result.rowcount or 0)


def override_rotation(rotation_id: int, *, operator_id: str = "operator@stockml", engine: Engine | None = None, now: datetime | None = None) -> bool:
    db = engine or get_engine(required=True)
    with db.begin() as conn:
        result = conn.execute(
            update(rotation_recommendation_log)
            .where(rotation_recommendation_log.c.id == rotation_id)
            .where(rotation_recommendation_log.c.verdict == "proposed")
            .values(verdict="overridden", operator_id=operator_id, operator_at=_aware(now))
        )
    return bool(result.rowcount)


def confirm_rotation(
    rotation_id: int,
    *,
    operator_id: str = "operator@stockml",
    engine: Engine | None = None,
    now: datetime | None = None,
    close_func: Callable[[str], dict[str, Any]] | None = None,
    open_func: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Confirm a Paper Assist rotation.

    The default implementation refuses to execute if no explicit paper open
    function is supplied. That keeps SPEC 47 operator-confirm only and prevents
    a hidden automatic open path from appearing before SPEC 48.
    """
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    with db.begin() as conn:
        row = conn.execute(
            select(rotation_recommendation_log)
            .where(rotation_recommendation_log.c.id == rotation_id)
            .where(rotation_recommendation_log.c.verdict == "proposed")
            .limit(1)
        ).mappings().first()
        if row is None:
            return {"status": "blocked", "message": "rotation_not_proposed"}
        if close_func is None or open_func is None:
            conn.execute(
                update(rotation_recommendation_log)
                .where(rotation_recommendation_log.c.id == rotation_id)
                .values(verdict="blocked", operator_id=operator_id, operator_at=stamp, details={**(row["details"] or {}), "block_reason": "paper_open_path_not_supplied"})
            )
            return {"status": "blocked", "message": "paper_open_path_not_supplied"}
        close_result = close_func(str(row["replace_symbol"]))
        if str(close_result.get("status", "")).lower() not in {"submitted", "dry_run", "recorded"}:
            conn.execute(
                update(rotation_recommendation_log)
                .where(rotation_recommendation_log.c.id == rotation_id)
                .values(verdict="blocked", operator_id=operator_id, operator_at=stamp, details={**(row["details"] or {}), "close_result": close_result})
            )
            return {"status": "blocked", "message": "close_failed", "close_result": close_result}
        open_result = open_func(str(row["with_symbol"]))
        if str(open_result.get("status", "")).lower() not in {"submitted", "dry_run", "recorded"}:
            conn.execute(
                update(rotation_recommendation_log)
                .where(rotation_recommendation_log.c.id == rotation_id)
                .values(verdict="blocked", operator_id=operator_id, operator_at=stamp, details={**(row["details"] or {}), "close_result": close_result, "open_result": open_result})
            )
            return {"status": "blocked", "message": "open_failed", "close_result": close_result, "open_result": open_result}
        conn.execute(
            update(rotation_recommendation_log)
            .where(rotation_recommendation_log.c.id == rotation_id)
            .values(verdict="confirmed", operator_id=operator_id, operator_at=stamp, details={**(row["details"] or {}), "close_result": close_result, "open_result": open_result})
        )
    record_event_safely(
        str(row["replace_position_id"] or position_id_for_symbol(str(row["replace_symbol"]))),
        "monitor_rotate",
        "paper_assist_rotation",
        {"replace_symbol": row["replace_symbol"], "with_symbol": row["with_symbol"], "rotation_id": rotation_id},
    )
    return {"status": "confirmed", "message": "rotation_confirmed", "close_result": close_result, "open_result": open_result}
