from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from stockml.trading.order_intent import derive_order_intent
from stockml.trading.session_mode import classify_session_mode

LINEAGE_FIELDS = (
    "pipeline_run_id",
    "cycle_id",
    "signal_id",
    "candidate_id",
    "scan_candidate_id",
    "parent_candidate_id",
    "event_key",
    "client_order_id",
    "broker_order_id",
    "position_id",
    "trade_id",
    "exit_decision_id",
    "order_intent",
    "strategy_mode",
    "session_mode",
    "event_session_mode",
    "planned_execution_session_mode",
    "actual_submission_session_mode",
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

SESSION_MODE_VALUES = {
    "regular_session",
    "pre_market",
    "after_hours",
    "overnight_24_5",
    "weekend_closed",
}

SESSION_MODE_ALIASES = {
    "regular": "regular_session",
    "market": "regular_session",
    "market_open": "regular_session",
    "premarket": "pre_market",
    "pre-market": "pre_market",
    "post_market": "after_hours",
    "post-market": "after_hours",
    "afterhours": "after_hours",
    "after-hours": "after_hours",
    "overnight": "overnight_24_5",
    "24x5": "overnight_24_5",
    "24/5": "overnight_24_5",
    "overnight_24x5": "overnight_24_5",
    "closed": "weekend_closed",
    "weekend": "weekend_closed",
}


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


def normalize_session_mode(value: Any) -> tuple[str, str]:
    text = _text(value).lower().replace(" ", "_")
    if not text:
        return "", ""
    normalized = SESSION_MODE_ALIASES.get(text, text)
    if normalized in SESSION_MODE_VALUES:
        return normalized, "" if normalized == text else "inconsistent_session_mode"
    return "", "inconsistent_session_mode"


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


def _session_mode_from_event_at(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return classify_session_mode(parsed)
    except Exception:
        return ""


def normalize_lineage(values: Mapping[str, Any], *, required: tuple[str, ...] = ()) -> LineageResult:
    out = {field: values.get(field) for field in LINEAGE_FIELDS}
    warnings = lineage_warning_for(out, required)
    for field in ("session_mode", "event_session_mode", "planned_execution_session_mode", "actual_submission_session_mode"):
        session_mode, session_warning = normalize_session_mode(out.get(field))
        if session_mode:
            out[field] = session_mode
        elif out.get(field):
            out[field] = ""
        if session_warning:
            warnings.append(f"{field}:{session_warning}")
    if not _text(out.get("event_session_mode")):
        derived = _session_mode_from_event_at(values.get("event_at") or values.get("timestamp"))
        if derived:
            out["event_session_mode"] = derived
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
    event_session_mode: Any = "",
    planned_execution_session_mode: Any = "",
    actual_submission_session_mode: Any = "",
    model_version: Any = "",
    side: Any = "",
    client_order_id: Any = "",
    candidate_id: Any = "",
    scan_candidate_id: Any = "",
    parent_candidate_id: Any = "",
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
    candidate_id = _text(candidate_id) or candidate_id_for(cycle_id=cycle_id, symbol=symbol_text, candidate_source=candidate_source_text)
    scan_candidate_id = _text(scan_candidate_id) or None
    parent_candidate_id = _text(parent_candidate_id) or None
    event_key = event_key_for(cycle_id=cycle_id, subject_id=candidate_id, event_type="selected", source=candidate_source_text)
    intent = derive_lineage_order_intent(current_qty=0, attempted_side=side, attempted_qty=1)
    values = {
        "pipeline_run_id": _text(pipeline_run_id) or None,
        "cycle_id": _text(cycle_id) or None,
        "signal_id": signal_id,
        "candidate_id": candidate_id,
        "scan_candidate_id": scan_candidate_id,
        "parent_candidate_id": parent_candidate_id,
        "event_key": event_key,
        "client_order_id": _text(client_order_id) or None,
        "broker_order_id": None,
        "position_id": None,
        "trade_id": None,
        "exit_decision_id": None,
        "order_intent": intent,
        "strategy_mode": strategy_text,
        "session_mode": _text(session_mode) or "regular_session",
        "event_session_mode": _text(event_session_mode) or None,
        "planned_execution_session_mode": _text(planned_execution_session_mode) or None,
        "actual_submission_session_mode": _text(actual_submission_session_mode) or None,
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
    broker_text = _text(broker_order_id) or _text(candidate.get("broker_order_id")) or _text(candidate.get("order_id"))
    existing_position_id = _text(candidate.get("position_id"))
    existing_trade_id = _text(candidate.get("trade_id"))
    if existing_position_id.lower().startswith("paper:"):
        existing_position_id = ""
    event_key = event_key_for(
        cycle_id=candidate.get("cycle_id"),
        subject_id=broker_text or client_order_id or candidate_id,
        event_type=event_type,
        source=candidate.get("candidate_source") or "paper_trader",
    )
    values = {field: candidate.get(field) for field in LINEAGE_FIELDS}
    values.update(
        {
            "candidate_id": candidate_id,
            "event_key": event_key,
            "client_order_id": _text(client_order_id) or None,
            "broker_order_id": broker_text or None,
            "position_id": existing_position_id or None,
            "trade_id": existing_trade_id or None,
            "order_intent": normalize_lineage_intent(candidate.get("order_intent")),
        }
    )
    required = ("cycle_id", "candidate_id", "client_order_id")
    if broker_text:
        required = (*required, "broker_order_id")
    return normalize_lineage(values, required=required)


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


def exit_decision_id_for(*, symbol: Any, position_id: Any = "", trade_id: Any = "", reason: Any = "", cycle_id: Any = "", event_at: Any = "") -> str | None:
    return stable_id("exit", _upper(symbol), position_id, trade_id, reason, cycle_id or event_at)


def exit_lineage(details: Mapping[str, Any], *, reason: Any = "", event_type: Any = "exit_decision") -> LineageResult:
    symbol = details.get("symbol") or details.get("ticker")
    values = {field: details.get(field) for field in LINEAGE_FIELDS}
    exit_reason = _text(reason) or _text(details.get("exit_reason")) or _text(details.get("reason")) or _text(details.get("decision_reason"))
    existing_exit = _text(details.get("exit_decision_id"))
    values.update(
        {
            "exit_decision_id": existing_exit
            or exit_decision_id_for(
                symbol=symbol,
                position_id=details.get("position_id"),
                trade_id=details.get("trade_id"),
                reason=exit_reason or event_type,
                cycle_id=details.get("cycle_id"),
                event_at=details.get("event_at") or details.get("timestamp"),
            ),
            "order_intent": normalize_lineage_intent(details.get("order_intent")),
        }
    )
    return normalize_lineage(values, required=("position_id", "trade_id", "exit_decision_id"))


def monitor_lineage(details: Mapping[str, Any]) -> LineageResult:
    values = {field: details.get(field) for field in LINEAGE_FIELDS}
    return normalize_lineage(values, required=("position_id", "trade_id"))


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
