from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from stockml.services.events import position_id_for_symbol
from stockml.trading.order_intent import derive_order_intent

LINEAGE_FIELDS = (
    "pipeline_run_id",
    "cycle_id",
    "signal_id",
    "candidate_id",
    "event_key",
    "client_order_id",
    "broker_order_id",
    "position_id",
    "trade_id",
    "exit_decision_id",
    "order_intent",
    "strategy_mode",
    "session_mode",
    "candidate_source",
    "model_version",
)

ORDER_INTENT_VALUES = {
    "open_long",
    "close_long",
    "reduce_long",
    "open_short",
    "cover_short",
    "reduce_short",
    "cancel_replace",
    "manual_close",
    "unknown",
}

OPEN_INTENTS = {"open_long", "open_short"}
CLOSE_INTENTS = {"close_long", "reduce_long", "cover_short", "reduce_short", "manual_close"}


@dataclass(frozen=True)
class LineageResult:
    values: dict[str, Any]
    warnings: list[str]


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def _upper(value: Any) -> str:
    return _text(value).upper()


def stable_id(prefix: str, *parts: Any) -> str | None:
    clean = [_text(part) for part in parts]
    if any(not part for part in clean):
        return None
    raw = "|".join(clean)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def normalize_lineage_intent(intent: Any) -> str:
    text = _text(intent).lower()
    if text in ORDER_INTENT_VALUES:
        return text
    if text == "increase_long":
        return "open_long"
    if text == "increase_short":
        return "open_short"
    if text == "close_long_then_reverse_short":
        return "close_long"
    if text == "cover_short_then_reverse_long":
        return "cover_short"
    return "unknown"


def derive_lineage_order_intent(*, current_qty: Any = 0, attempted_side: Any = "", attempted_qty: Any = 0, explicit: Any = "") -> str:
    explicit_text = normalize_lineage_intent(explicit)
    if explicit_text != "unknown":
        return explicit_text
    return normalize_lineage_intent(
        derive_order_intent(current_qty=current_qty, attempted_side=attempted_side, attempted_qty=attempted_qty).intent
    )


def candidate_id_for(*, cycle_id: Any, symbol: Any, candidate_source: Any) -> str | None:
    return stable_id("cand", cycle_id, _upper(symbol), candidate_source)


def signal_id_for(*, pipeline_run_id: Any, symbol: Any, strategy_mode: Any, model_version: Any) -> str | None:
    return stable_id("sig", pipeline_run_id, _upper(symbol), strategy_mode, model_version)


def trade_id_for(*, symbol: Any, broker_order_id: Any = "", client_order_id: Any = "", existing_trade_id: Any = "") -> str | None:
    existing = _text(existing_trade_id)
    if existing:
        return existing
    broker = _text(broker_order_id)
    client = _text(client_order_id)
    if broker:
        return f"trade-{broker}"
    if client:
        return stable_id("trade", _upper(symbol), client)
    return None


def lifecycle_position_id_for(*, symbol: Any, broker_order_id: Any = "", client_order_id: Any = "", existing_position_id: Any = "") -> str | None:
    existing = _text(existing_position_id)
    if existing and not existing.lower().startswith("paper:"):
        return existing
    broker = _text(broker_order_id)
    if broker:
        return f"position-{broker}"
    client = _text(client_order_id)
    if client:
        return stable_id("position", _upper(symbol), client)
    return None


def event_key_for(*, cycle_id: Any, subject_id: Any, event_type: Any, source: Any = "") -> str | None:
    return stable_id("evt", cycle_id, subject_id, event_type, source)


def lineage_warning_for(values: Mapping[str, Any], required: tuple[str, ...]) -> list[str]:
    return [f"missing_{field}" for field in required if not _text(values.get(field))]


def normalize_lineage(values: Mapping[str, Any], *, required: tuple[str, ...] = ()) -> LineageResult:
    out = {field: values.get(field) for field in LINEAGE_FIELDS}
    warnings = lineage_warning_for(out, required)
    existing_warning = _text(values.get("lineage_warning"))
    if existing_warning:
        warnings.extend(part for part in existing_warning.split("|") if part)
    out["lineage_warning"] = "|".join(dict.fromkeys(warnings)) if warnings else ""
    return LineageResult(values=out, warnings=warnings)


def candidate_lineage(
    *,
    symbol: Any,
    cycle_id: Any,
    pipeline_run_id: Any = "",
    candidate_source: Any = "paper_order_plan",
    strategy_mode: Any = "multi_day_forecast",
    session_mode: Any = "regular_session",
    model_version: Any = "",
    side: Any = "",
    client_order_id: Any = "",
) -> LineageResult:
    symbol_text = _upper(symbol)
    candidate_source_text = _text(candidate_source) or "paper_order_plan"
    strategy_text = _text(strategy_mode) or "multi_day_forecast"
    model_text = _text(model_version)
    signal_id = signal_id_for(
        pipeline_run_id=pipeline_run_id,
        symbol=symbol_text,
        strategy_mode=strategy_text,
        model_version=model_text,
    )
    candidate_id = candidate_id_for(cycle_id=cycle_id, symbol=symbol_text, candidate_source=candidate_source_text)
    event_key = event_key_for(cycle_id=cycle_id, subject_id=candidate_id, event_type="selected", source=candidate_source_text)
    intent = derive_lineage_order_intent(current_qty=0, attempted_side=side, attempted_qty=1)
    values = {
        "pipeline_run_id": _text(pipeline_run_id) or None,
        "cycle_id": _text(cycle_id) or None,
        "signal_id": signal_id,
        "candidate_id": candidate_id,
        "event_key": event_key,
        "client_order_id": _text(client_order_id) or None,
        "broker_order_id": None,
        "position_id": position_id_for_symbol(symbol_text) if symbol_text else None,
        "trade_id": None,
        "exit_decision_id": None,
        "order_intent": intent,
        "strategy_mode": strategy_text,
        "session_mode": _text(session_mode) or "regular_session",
        "candidate_source": candidate_source_text,
        "model_version": model_text or None,
    }
    return normalize_lineage(values, required=("cycle_id", "candidate_id", "event_key"))


def order_lineage(candidate: Mapping[str, Any], *, broker_order_id: Any = "", event_type: str = "submitted") -> LineageResult:
    symbol = candidate.get("symbol") or candidate.get("ticker")
    candidate_id = candidate.get("candidate_id") or candidate_id_for(
        cycle_id=candidate.get("cycle_id"),
        symbol=symbol,
        candidate_source=candidate.get("candidate_source") or "paper_order_plan",
    )
    client_order_id = candidate.get("client_order_id")
    trade_id = trade_id_for(symbol=symbol, broker_order_id=broker_order_id, client_order_id=client_order_id)
    event_key = event_key_for(
        cycle_id=candidate.get("cycle_id"),
        subject_id=_text(broker_order_id) or client_order_id or candidate_id,
        event_type=event_type,
        source=candidate.get("candidate_source") or "paper_trader",
    )
    values = {field: candidate.get(field) for field in LINEAGE_FIELDS}
    values.update(
        {
            "candidate_id": candidate_id,
            "event_key": event_key,
            "client_order_id": _text(client_order_id) or None,
            "broker_order_id": _text(broker_order_id) or _text(candidate.get("broker_order_id")) or None,
            "position_id": candidate.get("position_id") or (position_id_for_symbol(symbol) if _text(symbol) else None),
            "trade_id": trade_id,
            "order_intent": normalize_lineage_intent(candidate.get("order_intent")),
        }
    )
    return normalize_lineage(values, required=("cycle_id", "candidate_id", "client_order_id"))


def fill_lineage(tracked: Mapping[str, Any]) -> LineageResult:
    symbol = tracked.get("symbol") or tracked.get("ticker")
    broker_order_id = tracked.get("broker_order_id") or tracked.get("order_id")
    values = {field: tracked.get(field) for field in LINEAGE_FIELDS}
    values.update(
        {
            "broker_order_id": _text(broker_order_id) or None,
            "position_id": lifecycle_position_id_for(symbol=symbol, broker_order_id=broker_order_id, client_order_id=tracked.get("client_order_id"), existing_position_id=tracked.get("position_id")),
            "trade_id": trade_id_for(
                symbol=symbol,
                broker_order_id=broker_order_id,
                client_order_id=tracked.get("client_order_id"),
                existing_trade_id=tracked.get("trade_id"),
            ),
            "order_intent": normalize_lineage_intent(tracked.get("order_intent")),
        }
    )
    return normalize_lineage(values, required=("client_order_id", "broker_order_id", "position_id", "trade_id"))


def merge_lineage(payload: Mapping[str, Any], lineage: LineageResult | Mapping[str, Any]) -> dict[str, Any]:
    values = lineage.values if isinstance(lineage, LineageResult) else dict(lineage)
    out = dict(payload)
    for field in (*LINEAGE_FIELDS, "lineage_warning"):
        value = values.get(field)
        if value not in (None, ""):
            out[field] = value
        elif field not in out:
            out[field] = None if field in LINEAGE_FIELDS else ""
    return out
