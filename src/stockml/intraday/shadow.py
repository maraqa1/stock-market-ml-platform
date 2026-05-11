from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import shadow_would_trades
from stockml.intraday.features import IntradayFeatures, NightlySignal
from stockml.intraday.kill_switch import gate as kill_switch_gate


COOLOFF_MINUTES = 60
MARKET_IMPACT_BPS = 5.0
EVALUATION_TRADING_DAYS = 20


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def add_trading_days(start: date, days: int) -> date:
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def estimated_entry_slippage_bps(features: IntradayFeatures | dict | None) -> float:
    spread = _feature_value(features, "spread_bps")
    try:
        half_spread = max(float(spread or 0), 0.0) / 2.0
    except (TypeError, ValueError):
        half_spread = 0.0
    return half_spread + MARKET_IMPACT_BPS


def _feature_value(features: IntradayFeatures | dict | None, key: str) -> Any:
    if features is None:
        return None
    if isinstance(features, dict):
        return features.get(key)
    return getattr(features, key, None)


def _nightly_score(signal: NightlySignal | dict | None) -> float | None:
    raw = signal.score if isinstance(signal, NightlySignal) else (signal or {}).get("score")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _entry_price(features: IntradayFeatures | dict | None) -> float:
    for key in ("mid_price", "last_price", "entry_price"):
        value = _feature_value(features, key)
        if value is not None:
            return float(value)
    extra = _feature_value(features, "extra")
    if isinstance(extra, dict):
        for key in ("mid_price", "last_price", "entry_price"):
            if extra.get(key) is not None:
                return float(extra[key])
    raise ValueError("shadow would-trade requires an entry price")


def maybe_create_would_trade(
    conn: Connection,
    decision_row: dict[str, Any],
    features: IntradayFeatures | dict | None,
    nightly_signal: NightlySignal | dict | None = None,
    *,
    gate=None,
) -> dict[str, Any] | None:
    verdict = str(decision_row.get("verdict") or "")
    if verdict not in {"allow_long", "allow_short"}:
        return None

    gate = gate or kill_switch_gate
    kill_verdict = gate(action="would_trade")
    if not kill_verdict.allow:
        return None

    decision_id = decision_row.get("id")
    if decision_id is None:
        return None

    decided_at = _aware(decision_row["decided_at"])
    symbol = str(decision_row["symbol"]).upper()
    side = "long" if verdict == "allow_long" else "short"
    cooloff_start = decided_at - timedelta(minutes=COOLOFF_MINUTES)
    existing = conn.execute(
        select(shadow_would_trades.c.id)
        .where(
            and_(
                shadow_would_trades.c.symbol == symbol,
                shadow_would_trades.c.side == side,
                shadow_would_trades.c.status == "pending",
                shadow_would_trades.c.decided_at >= cooloff_start,
            )
        )
        .limit(1)
    ).first()
    if existing:
        return None

    row = {
        "decision_id": decision_id,
        "decided_at": decided_at,
        "symbol": symbol,
        "side": side,
        "entry_price": _entry_price(features),
        "estimated_entry_slippage_bps": estimated_entry_slippage_bps(features),
        "nightly_score": _nightly_score(nightly_signal),
        "gate_version": str(decision_row.get("gate_version") or "v1.0.0"),
        "evaluation_date": add_trading_days(decided_at.date(), EVALUATION_TRADING_DAYS),
        "status": "pending",
    }
    result = conn.execute(insert(shadow_would_trades).values(row))
    row["id"] = result.inserted_primary_key[0] if result.inserted_primary_key else None
    return row


def mark_superseded_for_position(
    symbol: str,
    side: str,
    opened_at: datetime,
    *,
    engine: Engine | None = None,
) -> int:
    db = engine or get_engine(required=False)
    if db is None:
        return 0
    stamp = _aware(opened_at)
    cooloff_start = stamp - timedelta(minutes=COOLOFF_MINUTES)
    with db.begin() as conn:
        result = conn.execute(
            update(shadow_would_trades)
            .where(
                and_(
                    shadow_would_trades.c.symbol == str(symbol).upper(),
                    shadow_would_trades.c.side == str(side).lower(),
                    shadow_would_trades.c.status == "pending",
                    shadow_would_trades.c.decided_at >= cooloff_start,
                    shadow_would_trades.c.decided_at <= stamp,
                )
            )
            .values(status="superseded")
        )
        return int(result.rowcount or 0)
