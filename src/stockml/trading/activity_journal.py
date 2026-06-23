from __future__ import annotations

from typing import Any, Mapping

from stockml.trading.lifecycle_ids import LINEAGE_FIELDS, LineageResult, merge_lineage, normalize_lineage


def enrich_activity_details(details: Mapping[str, Any] | None, lineage: LineageResult | Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(details or {})
    if lineage is not None:
        return merge_lineage(payload, lineage)
    normalized = normalize_lineage(payload)
    return merge_lineage(payload, normalized)


def lineage_from_activity(details: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    return {field: payload.get(field) for field in LINEAGE_FIELDS}
