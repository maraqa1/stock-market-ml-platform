from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import desc, select

from stockml.db.connection import get_engine
from stockml.db.schema import position_events
from stockml.services.events import position_id_for_symbol

from stockml.trading.lifecycle_ids import LINEAGE_FIELDS, LineageResult, exit_lineage, merge_lineage, monitor_lineage, normalize_lineage


def enrich_activity_details(details: Mapping[str, Any] | None, lineage: LineageResult | Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(details or {})
    if lineage is not None:
        return merge_lineage(payload, lineage)
    normalized = normalize_lineage(payload)
    return merge_lineage(payload, normalized)


def lineage_from_activity(details: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    return {field: payload.get(field) for field in LINEAGE_FIELDS}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def latest_trade_lineage_for_symbol(symbol: Any) -> dict[str, Any]:
    clean_symbol = _clean_text(symbol).upper()
    if not clean_symbol:
        return {"lineage_warning": "missing_symbol"}
    engine = get_engine(required=False)
    if engine is None:
        return {"lineage_warning": "missing_trade_id"}
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(position_events)
                .where(position_events.c.trade_id.is_not(None))
                .order_by(desc(position_events.c.event_at), desc(position_events.c.id))
                .limit(5000)
            )
            .mappings()
            .all()
        )
    for row in rows:
        data = dict(row)
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        row_symbol = _clean_text(details.get("symbol") or details.get("ticker")).upper()
        if not row_symbol:
            position_id = _clean_text(data.get("position_id") or details.get("position_id"))
            if ":" in position_id:
                row_symbol = position_id.rsplit(":", 1)[-1].upper()
        if row_symbol != clean_symbol:
            continue
        lineage = {field: data.get(field) or details.get(field) for field in LINEAGE_FIELDS}
        warnings = _clean_text(data.get("lineage_warning") or details.get("lineage_warning"))
        if warnings:
            lineage["lineage_warning"] = warnings
        return lineage
    return {"lineage_warning": "ambiguous_symbol_position|missing_trade_id"}

def _append_warning(existing: Any, warning: Any) -> str:
    parts = [part for part in _clean_text(existing).split("|") if part]
    for part in _clean_text(warning).split("|"):
        if part and part not in parts:
            parts.append(part)
    return "|".join(parts)


def enrich_monitor_activity_details(symbol: Any, details: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    latest = latest_trade_lineage_for_symbol(symbol)
    latest_warning = latest.get("lineage_warning")
    payload.update({k: v for k, v in latest.items() if k != "lineage_warning" and v not in (None, "") and not payload.get(k)})
    if latest_warning:
        payload["lineage_warning"] = _append_warning(payload.get("lineage_warning"), latest_warning)
    out = enrich_activity_details(payload, monitor_lineage(payload))
    if latest_warning:
        out["lineage_warning"] = _append_warning(out.get("lineage_warning"), latest_warning)
    return out


def enrich_exit_activity_details(symbol: Any, details: Mapping[str, Any] | None, *, reason: Any = "") -> dict[str, Any]:
    payload = dict(details or {})
    latest = latest_trade_lineage_for_symbol(symbol)
    latest_warning = latest.get("lineage_warning")
    payload.update({k: v for k, v in latest.items() if k != "lineage_warning" and v not in (None, "") and not payload.get(k)})
    if latest_warning:
        payload["lineage_warning"] = _append_warning(payload.get("lineage_warning"), latest_warning)
    out = enrich_activity_details(payload, exit_lineage(payload, reason=reason))
    if latest_warning:
        out["lineage_warning"] = _append_warning(out.get("lineage_warning"), latest_warning)
    return out
