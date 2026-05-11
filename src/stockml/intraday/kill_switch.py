from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine

from stockml.common.paths import PROJECT_ROOT
from stockml.db.connection import get_engine
from stockml.db.schema import kill_switch_events
from stockml.intraday.logging import intraday_log


CONFIG_PATH = PROJECT_ROOT / "config" / "kill_switches.yaml"
EVENT_TRIPPED = "tripped"
EVENT_RESUMED = "resumed"
ACTION_ORDER = {"evaluate": 0, "decide": 1, "would_trade": 2, "submit_order": 3}


class KillSwitchVerdict(NamedTuple):
    allow: bool
    tripped: list[str]
    tripped_at: datetime
    requires_manual_resume: bool
    cooloff_until: datetime | None
    cooloff_scope: str | None


@dataclass(frozen=True)
class KillSwitchConfig:
    version: int
    account_size_usd: float
    daily: dict[str, Any]
    weekly: dict[str, Any]
    total: dict[str, Any]
    position_limits: dict[str, Any]
    friction: dict[str, Any]


@dataclass(frozen=True)
class SwitchEvaluation:
    name: str
    tripped: bool
    current_value: Any
    threshold: Any
    payload: dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime:
    out = value or utc_now()
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def load_config(path: Path | str = CONFIG_PATH) -> KillSwitchConfig:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    required = ["version", "account_size_usd", "daily", "weekly", "total", "position_limits", "friction"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"Missing kill switch config keys: {', '.join(missing)}")
    return KillSwitchConfig(
        version=int(payload["version"]),
        account_size_usd=float(payload["account_size_usd"]),
        daily=dict(payload["daily"] or {}),
        weekly=dict(payload["weekly"] or {}),
        total=dict(payload["total"] or {}),
        position_limits=dict(payload["position_limits"] or {}),
        friction=dict(payload["friction"] or {}),
    )


def _engine(engine: Engine | None = None) -> Engine | None:
    return engine or get_engine(required=False)


def _event_rows(engine: Engine | None = None) -> list[dict[str, Any]]:
    db = _engine(engine)
    if db is None:
        return []
    try:
        with db.connect() as conn:
            return list(conn.execute(select(kill_switch_events).order_by(kill_switch_events.c.occurred_at.asc(), kill_switch_events.c.id.asc())).mappings())
    except Exception:
        return []


def _active_switches(engine: Engine | None = None) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for row in _event_rows(engine):
        name = str(row["switch_name"])
        if row["event_type"] == EVENT_TRIPPED:
            active[name] = dict(row)
        elif row["event_type"] == EVENT_RESUMED:
            active.pop(name, None)
    return active


def _already_active(switch_name: str, engine: Engine | None = None) -> bool:
    return switch_name in _active_switches(engine)


def _insert_event(
    switch_name: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    operator_id: str | None = None,
    notes: str | None = None,
    engine: Engine | None = None,
    now: datetime | None = None,
) -> None:
    db = _engine(engine)
    if db is None:
        intraday_log(f"kill_switch_{event_type}", {"switch_name": switch_name, **payload})
        return
    occurred_at = _aware(now)
    try:
        with db.begin() as conn:
            conn.execute(
                insert(kill_switch_events).values(
                    switch_name=switch_name,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    payload=payload,
                    operator_id=operator_id,
                    notes=notes,
                )
            )
    except Exception:
        intraday_log(
            f"kill_switch_{event_type}",
            {"switch_name": switch_name, "operator_id": operator_id, "notes": notes, **payload},
            now=occurred_at,
        )


def trip(switch_name: str, payload: dict[str, Any] | None = None, *, engine: Engine | None = None, now: datetime | None = None) -> None:
    if _already_active(switch_name, engine):
        return
    _insert_event(switch_name, EVENT_TRIPPED, dict(payload or {}), engine=engine, now=now)


def resume(
    switch_name: str,
    operator_id: str,
    notes: str,
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
) -> None:
    if not operator_id or not notes:
        raise ValueError("operator_id and notes are required to resume a kill switch")
    _insert_event(
        switch_name,
        EVENT_RESUMED,
        {"resumed": True},
        operator_id=operator_id,
        notes=notes,
        engine=engine,
        now=now,
    )


def _lte_metric(name: str, current: Any, threshold: Any) -> SwitchEvaluation:
    tripped = current is not None and threshold is not None and float(current) <= float(threshold)
    return SwitchEvaluation(name, tripped, current, threshold, {"current": current, "threshold": threshold})


def _gte_metric(name: str, current: Any, threshold: Any) -> SwitchEvaluation:
    tripped = current is not None and threshold is not None and float(current) >= float(threshold)
    return SwitchEvaluation(name, tripped, current, threshold, {"current": current, "threshold": threshold})


def _any_metric(name: str, current: Any, threshold: Any) -> SwitchEvaluation:
    tripped = bool(current) and threshold == "any"
    return SwitchEvaluation(name, tripped, current, threshold, {"current": current, "threshold": threshold})


def evaluate_switches(metrics: dict[str, Any] | None = None, config: KillSwitchConfig | None = None) -> list[SwitchEvaluation]:
    cfg = config or load_config()
    data = metrics or {}
    checks = [
        _lte_metric("daily.realized_plus_unrealized_loss_usd", data.get("daily_realized_plus_unrealized_loss_usd"), cfg.daily.get("realized_plus_unrealized_loss_usd")),
        _gte_metric("daily.consecutive_losing_trades", data.get("daily_consecutive_losing_trades"), cfg.daily.get("consecutive_losing_trades")),
        _gte_metric("daily.intraday_decisions_evaluated", data.get("daily_intraday_decisions_evaluated"), cfg.daily.get("intraday_decisions_evaluated")),
        _gte_metric("daily.unique_symbols_traded", data.get("daily_unique_symbols_traded"), cfg.daily.get("unique_symbols_traded")),
        _gte_metric("daily.provider_divergence_fires", data.get("daily_provider_divergence_fires"), cfg.daily.get("provider_divergence_fires")),
        _lte_metric("weekly.cumulative_loss_usd", data.get("weekly_cumulative_loss_usd"), cfg.weekly.get("cumulative_loss_usd")),
        _lte_metric("weekly.win_rate_last_20_min_pct", data.get("weekly_win_rate_last_20_min_pct"), cfg.weekly.get("win_rate_last_20_min_pct")),
        _lte_metric("weekly.drawdown_from_week_high_usd", data.get("weekly_drawdown_from_week_high_usd"), cfg.weekly.get("drawdown_from_week_high_usd")),
        _lte_metric("total.equity_floor_usd", data.get("total_equity_usd"), cfg.total.get("equity_floor_usd")),
        _lte_metric("total.single_position_loss_usd", data.get("total_single_position_loss_usd"), cfg.total.get("single_position_loss_usd")),
        _gte_metric("total.runaway_fills_per_5min", data.get("total_runaway_fills_per_5min"), cfg.total.get("runaway_fills_per_5min")),
        _any_metric("total.unauthorized_order_shape", data.get("total_unauthorized_order_shape"), cfg.total.get("unauthorized_order_shape")),
    ]
    return checks


def _action_allows_switch(action: str, switch_name: str) -> bool:
    level = ACTION_ORDER.get(action, ACTION_ORDER["evaluate"])
    if switch_name.startswith("total.unauthorized_order_shape"):
        return level >= ACTION_ORDER["submit_order"]
    return True


def gate(
    action: str = "evaluate",
    *,
    metrics: dict[str, Any] | None = None,
    engine: Engine | None = None,
    config: KillSwitchConfig | None = None,
    now: datetime | None = None,
) -> KillSwitchVerdict:
    stamp = _aware(now)
    cfg = config or load_config()
    active = _active_switches(engine)
    for evaluation in evaluate_switches(metrics, cfg):
        if evaluation.tripped and _action_allows_switch(action, evaluation.name):
            trip(evaluation.name, evaluation.payload, engine=engine, now=stamp)
            active[evaluation.name] = {
                "switch_name": evaluation.name,
                "event_type": EVENT_TRIPPED,
                "occurred_at": stamp,
                "payload": evaluation.payload,
            }
    tripped = sorted(active)
    cooloff_hours = float(cfg.friction.get("cooloff_after_stop_loss_hours") or 0)
    cooloff_until = stamp + timedelta(hours=cooloff_hours) if tripped and cooloff_hours else None
    cooloff_scope = f"{cfg.friction.get('cooloff_after_stop_loss_scope')}:*" if tripped and cfg.friction.get("cooloff_after_stop_loss_scope") else None
    return KillSwitchVerdict(
        allow=not tripped,
        tripped=tripped,
        tripped_at=stamp,
        requires_manual_resume=bool(tripped and cfg.friction.get("require_manual_resume_after_trip", True)),
        cooloff_until=cooloff_until,
        cooloff_scope=cooloff_scope,
    )


def state(*, engine: Engine | None = None, config: KillSwitchConfig | None = None, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    active = _active_switches(engine)
    evaluations = evaluate_switches(metrics, cfg)
    rows = []
    for evaluation in evaluations:
        rows.append(
            {
                "name": evaluation.name,
                "current_value": evaluation.current_value,
                "threshold": evaluation.threshold,
                "status": "tripped" if evaluation.name in active else "armed",
            }
        )
    return {
        "version": cfg.version,
        "account_size_usd": cfg.account_size_usd,
        "switches": rows,
        "active": sorted(active),
        "events": _event_rows(engine)[-20:],
    }
