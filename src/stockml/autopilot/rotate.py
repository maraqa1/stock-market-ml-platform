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

from stockml.common.paths import AGENT_DECISIONS_DIR, PORTAL_OUTPUTS_DIR, PROJECT_ROOT, latest_file
from stockml.db.connection import get_engine
from stockml.db.schema import rotation_recommendation_log
from stockml.intraday import kill_switch
from stockml.autopilot.open import apply_auto_open, latest_strong_candidates, load_auto_open_config
from stockml.autopilot.rotation_selector import OVERRIDE_REASON_TEXT, select_rotation_replacements
from stockml.services.events import position_id_for_symbol, record_event_safely
from stockml.trading.manual_position_actions import apply_manual_position_action


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
    edge_replacement_auto_enabled: bool = False
    edge_replacement_auto_dry_run: bool = True
    respect_monitor_verdict: bool = True
    monitor_override_score_delta: float = 0.20


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


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    return bool(value)


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
        require_operator_confirm=_bool(rotation.get("require_operator_confirm", True), True),
        edge_replacement_auto_enabled=_bool(rotation.get("edge_replacement_auto_enabled", False), False),
        edge_replacement_auto_dry_run=_bool(rotation.get("edge_replacement_auto_dry_run", True), True),
        respect_monitor_verdict=_bool(rotation.get("respect_monitor_verdict", True), True),
        monitor_override_score_delta=float(rotation.get("monitor_override_score_delta", 0.20)),
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
    out: list[Rotation] = []
    selections = select_rotation_replacements(
        open_positions,
        promoted,
        open_positions=open_positions,
        min_score_delta=cfg.min_score_delta,
        min_hold_minutes=cfg.min_hold_minutes,
        respect_monitor_verdict=cfg.respect_monitor_verdict,
        monitor_override_score_delta=cfg.monitor_override_score_delta,
        now=stamp,
    )
    for selection in selections:
        weaker = selection.held
        candidate = selection.candidate
        symbol = _text(candidate.get("symbol")).upper()
        replace = _text(weaker.get("symbol")).upper()
        out.append(
            Rotation(
                replace_symbol=replace,
                with_symbol=symbol,
                replace_position_id=str(weaker.get("position_id") or position_id_for_symbol(replace)),
                promotion_score=selection.promotion_score,
                held_score=selection.held_score,
                score_delta=selection.score_delta,
                reason=_derive_reason(candidate, weaker),
                details={
                    "require_operator_confirm": cfg.require_operator_confirm,
                    "monitor_verdict": selection.monitor_verdict,
                    "monitor_override": selection.monitor_override,
                    "reason_text": OVERRIDE_REASON_TEXT if selection.monitor_override else "",
                },
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


def _proposed_rotation_rows(engine: Engine, limit: int) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(rotation_recommendation_log)
            .where(rotation_recommendation_log.c.verdict == "proposed")
            .order_by(rotation_recommendation_log.c.score_delta.desc(), rotation_recommendation_log.c.logged_at.asc())
            .limit(limit)
        ).mappings().all()
    return [dict(row) for row in rows]


def _rotation_candidate(row: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    with_symbol = _text(row.get("with_symbol")).upper()
    for candidate in candidates:
        if _text(candidate.get("symbol")).upper() != with_symbol:
            continue
        payload = dict(candidate)
        bias = _bias(payload.get("nightly_bias") or payload.get("bias") or "long")
        side = "sell" if bias == "short" else "buy"
        action = "Short" if bias == "short" else "Long"
        details = dict(payload.get("details") or {})
        details.update(
            {
                "rotation_replacement": True,
                "replace_symbol": _text(row.get("replace_symbol")).upper(),
                "rotation_id": row.get("id"),
                "latest_signal_status": "fresh",
                "latest_signal_direction": bias,
                "model_status": "decision_grade",
                "current_trade_action": action,
                "side": side,
            }
        )
        payload.update({"details": details, "side": side, "current_trade_action": action})
        return payload
    return None


def _root_path(root: Path | str | None = None) -> Path:
    return Path(root) if root is not None else PROJECT_ROOT


def _artifact_dir(root: Path | str | None, relative: str, default: Path) -> Path:
    return _root_path(root) / relative if root is not None else default


def _latest_csv(directory: Path, pattern: str) -> pd.DataFrame:
    path = latest_file(directory, pattern)
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _clean_record(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    items = row.items() if isinstance(row, dict) else row.to_dict().items()
    out: dict[str, Any] = {}
    for key, value in items:
        try:
            if pd.isna(value):
                value = ""
        except Exception:
            pass
        out[str(key)] = value
    return out


def _candidate_side(candidate: dict[str, Any]) -> tuple[str, str]:
    bias = _bias(candidate.get("trade_action") or candidate.get("current_trade_action") or candidate.get("nightly_bias") or candidate.get("side"))
    if bias == "short":
        return "short", "sell"
    return "long", "buy"


def _candidate_score(candidate: dict[str, Any], decision: dict[str, Any]) -> float:
    for key in ["promotion_score", "risk_adjusted_score", "side_probability", "confidence_score", "score"]:
        value = candidate.get(key)
        if _text(value):
            return _float(value)
    return _float(decision.get("replacement_score"))


def _edge_replacement_candidate(decision: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    bias, side = _candidate_side(candidate)
    trade_action = "Short" if bias == "short" else "Long"
    details = dict(candidate)
    details.update(
        {
            "rotation_replacement": True,
            "edge_replacement": True,
            "replace_symbol": _text(decision.get("symbol")).upper(),
            "replacement_reason": _text(decision.get("replacement_reason")) or "stronger_edge_candidate_available",
            "replacement_edge_bps": _float(decision.get("replacement_edge_bps")),
            "latest_signal_status": "fresh",
            "latest_signal_direction": bias,
            "model_status": "decision_grade",
            "current_trade_action": trade_action,
            "side": side,
            "nightly_bias": bias,
        }
    )
    return {
        **candidate,
        "symbol": _text(candidate.get("symbol")).upper(),
        "promotion_score": _candidate_score(candidate, decision),
        "nightly_bias": bias,
        "trade_action": trade_action,
        "directional_action": trade_action,
        "current_trade_action": trade_action,
        "side": side,
        "is_held": False,
        "details": details,
    }


def _edge_replacement_candidates(
    open_positions: list[dict[str, Any]],
    *,
    root: Path | str | None = None,
    limit: int = 3,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    decisions_dir = _artifact_dir(root, "data/trading/agent_decisions", AGENT_DECISIONS_DIR)
    candidates_dir = _artifact_dir(root, "data/portal_outputs", PORTAL_OUTPUTS_DIR)
    decisions = _latest_csv(decisions_dir, "position_decisions_*.csv")
    pool = _latest_csv(candidates_dir, "08_alpaca_paper_candidate_pool_*.csv")
    if decisions.empty or pool.empty or "symbol" not in decisions.columns or "symbol" not in pool.columns:
        return []

    held = _held_symbols(open_positions)
    decisions = decisions.copy()
    pool = pool.copy()
    decisions["__symbol"] = decisions["symbol"].fillna("").astype(str).str.upper()
    decisions["__replacement"] = decisions.get("replacement_symbol", pd.Series("", index=decisions.index)).fillna("").astype(str).str.upper()
    pool["__symbol"] = pool["symbol"].fillna("").astype(str).str.upper()
    status = pool.get("trade_quality_status", pd.Series("", index=pool.index)).fillna("").astype(str).str.lower()
    eligible = pool.get("order_eligible", pd.Series(False, index=pool.index)).map(_bool)
    qty = pd.to_numeric(pool.get("suggested_quantity", pd.Series(0, index=pool.index)), errors="coerce").fillna(0)
    pool = pool[status.isin({"approved", "reduced"}) & eligible & (qty >= 1)].copy()
    if pool.empty:
        return []

    decision_filter = (
        decisions.get("decision", pd.Series("", index=decisions.index)).fillna("").astype(str).str.lower().eq("replace")
        & decisions.get("recommended_action", pd.Series("", index=decisions.index)).fillna("").astype(str).str.lower().eq("review_edge_replacement")
        & decisions["__symbol"].isin(held)
        & decisions["__replacement"].ne("")
        & ~decisions["__replacement"].isin(held)
    )
    decisions = decisions[decision_filter].copy()
    if decisions.empty:
        return []

    decisions["__edge"] = pd.to_numeric(decisions.get("replacement_edge_bps", pd.Series(0, index=decisions.index)), errors="coerce").fillna(0)
    decisions = decisions.sort_values(["__edge", "__symbol"], ascending=[False, True]).head(max(0, int(limit)))

    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for _, decision_row in decisions.iterrows():
        decision = _clean_record(decision_row)
        replacement_symbol = _text(decision.get("__replacement")).upper()
        matches = pool[pool["__symbol"].eq(replacement_symbol)]
        if matches.empty:
            continue
        candidate = _clean_record(matches.iloc[0])
        decision_bias = _bias(decision.get("side"))
        candidate_bias, _ = _candidate_side(candidate)
        if decision_bias and candidate_bias and decision_bias != candidate_bias:
            continue
        out.append((decision, _edge_replacement_candidate(decision, candidate)))
    return out


def apply_auto_rotations(
    open_positions: list[dict[str, Any]],
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
    root: Path | str | None = None,
    close_func: Callable[[str], dict[str, Any]] | None = None,
    open_func: Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    cfg = load_rotation_config()
    if not cfg.enabled or cfg.require_operator_confirm:
        return {"auto_rotations_attempted": 0, "auto_rotations_confirmed": 0, "auto_rotation_notes": "operator_confirmation_required" if cfg.require_operator_confirm else "rotation_disabled"}

    proposed = _proposed_rotation_rows(db, cfg.max_rotations_per_day)
    if not proposed:
        if not cfg.edge_replacement_auto_enabled:
            return {
                "auto_rotations_attempted": 0,
                "auto_rotations_confirmed": 0,
                "auto_rotation_notes": "",
                "auto_edge_replacements_attempted": 0,
                "auto_edge_replacements_confirmed": 0,
                "auto_edge_replacements_blocked": 0,
                "auto_edge_replacements_dry_run": 0,
            }
        proposed = []

    candidates = latest_strong_candidates(engine=db, limit=100) if proposed else []
    close_position = close_func or (lambda symbol: apply_manual_position_action(symbol, "close"))
    open_replacement = open_func or (
        lambda rows, positions: apply_auto_open(
            rows,
            positions,
            mode="paper_autopilot",
            engine=db,
            config=load_auto_open_config(),
            now=stamp,
        )
    )
    attempted = 0
    confirmed = 0
    edge_attempted = 0
    edge_confirmed = 0
    edge_blocked = 0
    edge_dry_run = 0
    notes: list[str] = []
    consumed_replacements: set[str] = set()
    consumed_replaces: set[str] = set()
    for row in proposed:
        replace_symbol = _text(row.get("replace_symbol")).upper()
        with_symbol = _text(row.get("with_symbol")).upper()
        if not replace_symbol or not with_symbol or replace_symbol in consumed_replaces or with_symbol in consumed_replacements:
            continue
        candidate = _rotation_candidate(row, candidates)
        attempted += 1
        if candidate is None:
            with db.begin() as conn:
                conn.execute(
                    update(rotation_recommendation_log)
                    .where(rotation_recommendation_log.c.id == row["id"])
                    .values(verdict="blocked", operator_id="paper_autopilot", operator_at=stamp, details={**(row.get("details") or {}), "block_reason": "replacement_candidate_not_found"})
                )
            notes.append(f"{replace_symbol}->{with_symbol}:blocked:replacement_candidate_not_found")
            continue
        positions_after_replace = [pos for pos in open_positions if _text(pos.get("symbol")).upper() != replace_symbol]
        open_result = open_replacement([candidate], positions_after_replace)
        if int(open_result.get("autopilot_open_submitted") or 0) <= 0:
            with db.begin() as conn:
                conn.execute(
                    update(rotation_recommendation_log)
                    .where(rotation_recommendation_log.c.id == row["id"])
                    .values(verdict="blocked", operator_id="paper_autopilot", operator_at=stamp, details={**(row.get("details") or {}), "open_result": open_result})
                )
            notes.append(f"{replace_symbol}->{with_symbol}:blocked:{open_result.get('autopilot_open_notes') or 'open_not_submitted'}")
            continue
        close_result = close_position(replace_symbol)
        if str(close_result.get("status") or "").lower() not in {"submitted", "dry_run", "recorded"}:
            with db.begin() as conn:
                conn.execute(
                    update(rotation_recommendation_log)
                    .where(rotation_recommendation_log.c.id == row["id"])
                    .values(verdict="blocked", operator_id="paper_autopilot", operator_at=stamp, details={**(row.get("details") or {}), "open_result": open_result, "close_result": close_result})
                )
            notes.append(f"{replace_symbol}->{with_symbol}:open_submitted_close_failed")
            continue
        with db.begin() as conn:
            conn.execute(
                update(rotation_recommendation_log)
                .where(rotation_recommendation_log.c.id == row["id"])
                .values(verdict="confirmed", operator_id="paper_autopilot", operator_at=stamp, details={**(row.get("details") or {}), "open_result": open_result, "close_result": close_result})
            )
        consumed_replacements.add(with_symbol)
        consumed_replaces.add(replace_symbol)
        confirmed += 1
        notes.append(f"{replace_symbol}->{with_symbol}:confirmed")
        record_event_safely(
            str(row.get("replace_position_id") or position_id_for_symbol(replace_symbol)),
            "monitor_rotate",
            "paper_autopilot_rotation",
            {"replace_symbol": replace_symbol, "with_symbol": with_symbol, "rotation_id": row.get("id")},
        )

    remaining = max(0, cfg.max_rotations_per_day - confirmed)
    if cfg.edge_replacement_auto_enabled and remaining > 0:
        for decision, candidate in _edge_replacement_candidates(open_positions, root=root, limit=remaining):
            replace_symbol = _text(decision.get("symbol")).upper()
            with_symbol = _text(candidate.get("symbol")).upper()
            if not replace_symbol or not with_symbol or replace_symbol in consumed_replaces or with_symbol in consumed_replacements:
                continue
            attempted += 1
            edge_attempted += 1
            if cfg.edge_replacement_auto_dry_run:
                edge_dry_run += 1
                notes.append(f"{replace_symbol}->{with_symbol}:edge_dry_run")
                continue
            positions_after_replace = [pos for pos in open_positions if _text(pos.get("symbol")).upper() != replace_symbol]
            open_result = open_replacement([candidate], positions_after_replace)
            if int(open_result.get("autopilot_open_submitted") or 0) <= 0:
                edge_blocked += 1
                notes.append(f"{replace_symbol}->{with_symbol}:edge_blocked:{open_result.get('autopilot_open_notes') or 'open_not_submitted'}")
                continue
            close_result = close_position(replace_symbol)
            if str(close_result.get("status") or "").lower() not in {"submitted", "dry_run", "recorded"}:
                edge_blocked += 1
                notes.append(f"{replace_symbol}->{with_symbol}:edge_open_submitted_close_failed")
                continue
            consumed_replacements.add(with_symbol)
            consumed_replaces.add(replace_symbol)
            confirmed += 1
            edge_confirmed += 1
            notes.append(f"{replace_symbol}->{with_symbol}:edge_confirmed")
            record_event_safely(
                str(decision.get("position_id") or position_id_for_symbol(replace_symbol)),
                "monitor_rotate",
                "paper_autopilot_edge_replacement",
                {
                    "replace_symbol": replace_symbol,
                    "with_symbol": with_symbol,
                    "replacement_edge_bps": decision.get("replacement_edge_bps"),
                    "replacement_reason": decision.get("replacement_reason"),
                },
            )
    return {
        "auto_rotations_attempted": attempted,
        "auto_rotations_confirmed": confirmed,
        "auto_rotation_notes": "; ".join(notes[:10]),
        "auto_edge_replacements_attempted": edge_attempted,
        "auto_edge_replacements_confirmed": edge_confirmed,
        "auto_edge_replacements_blocked": edge_blocked,
        "auto_edge_replacements_dry_run": edge_dry_run,
    }
