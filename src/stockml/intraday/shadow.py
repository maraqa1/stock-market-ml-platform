from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, insert, select, update
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import price_history, shadow_outcomes, shadow_would_trades
from stockml.intraday.features import IntradayFeatures, NightlySignal
from stockml.intraday.kill_switch import gate as kill_switch_gate


COOLOFF_MINUTES = 60
MARKET_IMPACT_BPS = 5.0
EVALUATION_TRADING_DAYS = 20
SPY_SYMBOL = "SPY"


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


def _price_on_or_before(conn: Connection, symbol: str, selected_date: date) -> float | None:
    row = conn.execute(
        select(price_history.c.adj_close, price_history.c.close)
        .where(price_history.c.ticker == str(symbol).upper())
        .where(price_history.c.date <= selected_date)
        .order_by(desc(price_history.c.date))
        .limit(1)
    ).first()
    if not row:
        return None
    for value in row:
        if value is not None:
            return float(value)
    return None


def _raw_return(side: str, entry_price: float, exit_price: float) -> float:
    if entry_price == 0:
        return 0.0
    if side == "short":
        return (entry_price - exit_price) / entry_price
    return (exit_price - entry_price) / entry_price


def evaluate_pending_outcomes(
    *,
    as_of_date: date | None = None,
    evaluated_at: datetime | None = None,
    engine: Engine | None = None,
    price_loader=None,
    spy_symbol: str = SPY_SYMBOL,
) -> dict[str, int]:
    db = engine or get_engine(required=False)
    if db is None:
        return {"evaluated": 0, "skipped_missing_price": 0}

    selected_date = as_of_date or datetime.now(timezone.utc).date()
    stamp = _aware(evaluated_at or datetime.now(timezone.utc))
    evaluated = 0
    skipped = 0

    with db.begin() as conn:
        loader = price_loader or _price_on_or_before
        rows = conn.execute(
            select(shadow_would_trades)
            .where(shadow_would_trades.c.status == "pending")
            .where(shadow_would_trades.c.evaluation_date <= selected_date)
            .order_by(shadow_would_trades.c.evaluation_date, shadow_would_trades.c.id)
        ).mappings().all()

        for trade in rows:
            existing = conn.execute(select(shadow_outcomes.c.would_trade_id).where(shadow_outcomes.c.would_trade_id == trade["id"])).first()
            if existing:
                conn.execute(update(shadow_would_trades).where(shadow_would_trades.c.id == trade["id"]).values(status="evaluated"))
                continue

            exit_price = loader(conn, trade["symbol"], trade["evaluation_date"])
            spy_entry = loader(conn, spy_symbol, trade["decided_at"].date())
            spy_exit = loader(conn, spy_symbol, trade["evaluation_date"])
            if exit_price is None or spy_entry is None or spy_exit is None:
                skipped += 1
                continue

            raw = _raw_return(str(trade["side"]), float(trade["entry_price"]), float(exit_price))
            cost_bps = float(trade["estimated_entry_slippage_bps"] or 0.0) * 2.0
            net = raw - cost_bps / 10_000.0
            spy_return = _raw_return("long", float(spy_entry), float(spy_exit))
            net_excess = net - spy_return
            conn.execute(
                insert(shadow_outcomes).values(
                    would_trade_id=trade["id"],
                    evaluated_at=stamp,
                    exit_price=float(exit_price),
                    raw_return_pct=raw,
                    cost_bps=cost_bps,
                    net_return_pct=net,
                    spy_return_pct=spy_return,
                    net_excess_pct=net_excess,
                    outperformed=net_excess > 0,
                )
            )
            conn.execute(update(shadow_would_trades).where(shadow_would_trades.c.id == trade["id"]).values(status="evaluated"))
            evaluated += 1

    return {"evaluated": evaluated, "skipped_missing_price": skipped}
