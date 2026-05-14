from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import intraday_candidate_snapshots, intraday_promotion_log
from stockml.intraday.promotion_gate import PROMOTION_RULES, evaluate_promotion_gate, load_promotion_config


@dataclass(frozen=True)
class PromotionDecision:
    symbol: str
    verdict: str
    block_reason: str | None
    nightly_score: float | None
    intraday_adjustment: float
    promotion_score: float
    contributing: list[str]


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def _float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key, default)
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def directional_nightly_score(row: dict[str, Any]) -> float:
    raw_score = _float(row, "nightly_score")
    bias = str(row.get("nightly_bias") or "").strip().lower()
    if bias == "short":
        return abs(raw_score)
    return raw_score


def intraday_adjustment(row: dict[str, Any]) -> tuple[float, list[str]]:
    adjustment = 0.0
    contributing: list[str] = []
    details = row.get("details") or {}
    bias = str(row.get("nightly_bias") or "").strip().lower()
    trend_5m = _float(row, "trend_5m_pct")
    trend_15m = _float(row, "trend_15m_pct")
    volume_ratio = _float(details, "volume_ratio")
    range_position = _float(row, "intraday_range_position")
    sector_trend = _float(row, "sector_etf_trend_5m_pct")
    spread_z = _float(details, "spread_bps_zscore_20d")
    distance_vwap = _float(row, "distance_from_vwap_bps")

    if (bias == "short" and trend_5m < -1) or (bias != "short" and trend_5m > 1):
        adjustment += 0.05
        contributing.append("score_trend_5m_bonus")
    if (bias == "short" and trend_15m < -2) or (bias != "short" and trend_15m > 2):
        adjustment += 0.05
        contributing.append("score_trend_15m_bonus")
    if volume_ratio > 1.5:
        adjustment += 0.03
        contributing.append("score_volume_bonus")
    if (bias == "short" and range_position < 0.3) or (bias != "short" and range_position > 0.7):
        adjustment += 0.03
        contributing.append("score_range_bonus")
    if row.get("market_aligned") is True and ((bias == "short" and sector_trend < -0.5) or (bias != "short" and sector_trend > 0.5)):
        adjustment += 0.02
        contributing.append("score_sector_bonus")
    if bool(row.get("volatility_burst")):
        adjustment -= 0.05
        contributing.append("score_volatility_penalty")
    if spread_z > 2:
        adjustment -= 0.05
        contributing.append("score_spread_penalty")
    if (bias == "short" and distance_vwap > 100) or (bias != "short" and distance_vwap < -100):
        adjustment -= 0.03
        contributing.append("score_vwap_penalty")
    return _clip(adjustment, -0.2, 0.2), contributing


def evaluate_snapshot(row: dict[str, Any], *, recent_action_taken: bool = False) -> PromotionDecision:
    gate = evaluate_promotion_gate(row, recent_action_taken=recent_action_taken)
    nightly_score = row.get("nightly_score")
    base_score = directional_nightly_score(row)
    adjustment, score_rules = intraday_adjustment(row)
    promotion_score = _clip(base_score + adjustment)
    contributing = [rule for rule in [*gate.contributing, *score_rules] if rule in PROMOTION_RULES]
    if gate.blocked:
        return PromotionDecision(
            symbol=str(row.get("symbol") or "").upper(),
            verdict="block",
            block_reason=gate.block_reason.value if gate.block_reason else None,
            nightly_score=nightly_score,
            intraday_adjustment=adjustment,
            promotion_score=promotion_score,
            contributing=contributing,
        )
    if not gate.confirmed:
        verdict = "watch"
    else:
        cfg = load_promotion_config()
        if promotion_score >= cfg.strong_selection_threshold:
            verdict = "promote_to_selection_strong"
        elif promotion_score >= cfg.selection_threshold:
            verdict = "promote_to_selection"
        else:
            verdict = "watch"
    return PromotionDecision(
        symbol=str(row.get("symbol") or "").upper(),
        verdict=verdict,
        block_reason=None,
        nightly_score=nightly_score,
        intraday_adjustment=adjustment,
        promotion_score=promotion_score,
        contributing=contributing,
    )


def _recent_action_taken(row: dict[str, Any], *, engine: Engine, now: datetime) -> bool:
    symbol = str(row.get("symbol") or "").upper()
    if not symbol:
        return False
    cutoff = now - timedelta(minutes=load_promotion_config().symbol_cooloff_minutes)
    with engine.connect() as conn:
        found = conn.execute(
            select(intraday_promotion_log.c.id)
            .where(intraday_promotion_log.c.symbol == symbol)
            .where(intraday_promotion_log.c.logged_at >= cutoff)
            .where(intraday_promotion_log.c.verdict.in_(("promote_to_selection", "promote_to_selection_strong")))
            .limit(1)
        ).first()
    return bool(found)


def record_promotion_decision(
    snapshot_id: int,
    decision: PromotionDecision,
    *,
    engine: Engine | None = None,
    logged_at: datetime | None = None,
) -> int | None:
    db = engine or get_engine(required=True)
    stamp = _aware(logged_at)
    with db.begin() as conn:
        existing = conn.execute(
            select(intraday_promotion_log.c.id).where(intraday_promotion_log.c.snapshot_id == snapshot_id).limit(1)
        ).first()
        if existing:
            return existing[0]
        result = conn.execute(
            insert(intraday_promotion_log).values(
                logged_at=stamp,
                snapshot_id=snapshot_id,
                symbol=decision.symbol,
                verdict=decision.verdict,
                block_reason=decision.block_reason,
                nightly_score=decision.nightly_score,
                intraday_adjustment=decision.intraday_adjustment,
                promotion_score=decision.promotion_score,
                contributing=decision.contributing,
            )
        )
        return result.inserted_primary_key[0] if result.inserted_primary_key else None


def score_unscored_snapshots(*, engine: Engine | None = None, now: datetime | None = None, limit: int = 1000) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    with db.connect() as conn:
        rows = conn.execute(
            select(intraday_candidate_snapshots)
            .where(
                ~intraday_candidate_snapshots.c.id.in_(
                    select(intraday_promotion_log.c.snapshot_id)
                )
            )
            .order_by(intraday_candidate_snapshots.c.snapshot_at.asc(), intraday_candidate_snapshots.c.symbol.asc())
            .limit(limit)
        ).mappings().all()
    written = 0
    verdict_counts: dict[str, int] = {}
    for row in rows:
        payload = dict(row)
        decision = evaluate_snapshot(payload, recent_action_taken=_recent_action_taken(payload, engine=db, now=stamp))
        if record_promotion_decision(int(payload["id"]), decision, engine=db, logged_at=stamp) is not None:
            written += 1
            verdict_counts[decision.verdict] = verdict_counts.get(decision.verdict, 0) + 1
    return {"status": "ok", "snapshots_scored": written, "verdict_counts": verdict_counts}


def explain_latest_snapshot(symbol: str, *, engine: Engine | None = None) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        return {"status": "error", "reason": "symbol_required"}
    with db.connect() as conn:
        row = conn.execute(
            select(intraday_candidate_snapshots)
            .where(intraday_candidate_snapshots.c.symbol == clean_symbol)
            .order_by(intraday_candidate_snapshots.c.snapshot_at.desc(), intraday_candidate_snapshots.c.id.desc())
            .limit(1)
        ).mappings().first()
    if row is None:
        return {"status": "missing", "symbol": clean_symbol}
    payload = dict(row)
    decision = evaluate_snapshot(payload)
    return {
        "status": "ok",
        "symbol": clean_symbol,
        "snapshot_id": payload.get("id"),
        "snapshot_at": payload.get("snapshot_at"),
        "nightly_bias": payload.get("nightly_bias"),
        "nightly_score": payload.get("nightly_score"),
        "directional_score": directional_nightly_score(payload),
        "spread_bps": payload.get("spread_bps"),
        "dollar_volume_today": payload.get("dollar_volume_today"),
        "trend_5m_pct": payload.get("trend_5m_pct"),
        "trend_15m_pct": payload.get("trend_15m_pct"),
        "intraday_range_position": payload.get("intraday_range_position"),
        "decision": decision,
    }
