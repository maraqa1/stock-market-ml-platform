from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import intraday_decisions
from stockml.intraday.features import IntradayFeatures, NightlySignal
from stockml.intraday.gates import GATE_VERSION, GateDecision, next_five_minute_boundary
from stockml.intraday.logging import intraday_log
from stockml.intraday.shadow import maybe_create_would_trade


def _aware(value: datetime | None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def _serializable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _aware(value).isoformat(timespec="seconds")
    if is_dataclass(value):
        return {key: _serializable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    return value


def nightly_signal_payload(signal: NightlySignal | dict | None) -> dict[str, Any] | None:
    if signal is None:
        return None
    return _serializable(signal)


def feature_payload(features: IntradayFeatures | dict | None) -> dict[str, Any]:
    if features is None:
        return {}
    return _serializable(features)


def record_decision(
    symbol: str,
    features: IntradayFeatures | dict | None,
    decision: GateDecision | None,
    nightly_signal: NightlySignal | dict | None = None,
    *,
    engine: Engine | None = None,
    decided_at: datetime | None = None,
    bar_close_at: datetime | None = None,
    status: str | None = None,
    create_shadow: bool = True,
) -> dict[str, Any]:
    stamp = _aware(decided_at or getattr(features, "decided_at", None))
    verdict = status or (decision.verdict if decision else "data_unavailable")
    valid_until = decision.valid_until if decision else next_five_minute_boundary(stamp)
    row = {
        "decided_at": stamp,
        "symbol": str(symbol).upper(),
        "bar_close_at": _aware(bar_close_at or stamp),
        "verdict": verdict,
        "block_reason": decision.block_reason.value if decision and decision.block_reason else None,
        "gate_version": decision.gate_version if decision else GATE_VERSION,
        "valid_until": _aware(valid_until),
        "nightly_signal": nightly_signal_payload(nightly_signal),
        "features": feature_payload(features),
        "contributing": list(decision.contributing) if decision else [],
    }
    db = engine or get_engine(required=False)
    if db is not None:
        try:
            with db.begin() as conn:
                result = conn.execute(insert(intraday_decisions).values(row))
                row["id"] = result.inserted_primary_key[0] if result.inserted_primary_key else None
                if create_shadow:
                    shadow = maybe_create_would_trade(conn, row, features, nightly_signal)
                    if shadow:
                        row["shadow_would_trade_id"] = shadow.get("id")
                return row
        except Exception:
            pass
    intraday_log(
        "intraday_decision",
        {
            **{key: _serializable(value) for key, value in row.items()},
            "fallback": "db_unavailable",
        },
        now=stamp,
    )
    row["id"] = None
    return row
