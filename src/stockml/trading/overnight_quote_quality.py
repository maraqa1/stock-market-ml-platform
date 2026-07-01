from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from stockml.trading.spread_edge import evaluate_spread_edge, expected_move_bps_from


@dataclass(frozen=True)
class QuoteQualityResult:
    ok: bool
    spread_bps: float | None
    freshness_seconds: float | None
    reason: str = ""
    executable_price: float | None = None
    reference_price: float | None = None
    executable_price_deviation_bps: float | None = None
    expected_move_bps: float | None = None
    estimated_cost_bps: float | None = None
    expected_net_edge_bps: float | None = None
    edge_to_spread_ratio: float | None = None
    spread_gate_decision: str = ""


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _aware(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = pd.to_datetime(value, utc=True).to_pydatetime()
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate_quote_quality(
    data: dict[str, Any],
    *,
    max_spread_bps: float,
    max_freshness_seconds: float = 900.0,
    max_executable_deviation_bps: float | None = None,
    estimated_cost_bps: float = 10.0,
    min_edge_to_spread_ratio: float = 3.0,
    min_expected_net_edge_bps: float = 25.0,
    now: datetime | None = None,
    require_fresh_quote: bool = False,
) -> QuoteQualityResult:
    spread = _float(data.get("spread_bps"))
    bid = _float(data.get("bid") or data.get("bid_price"))
    ask = _float(data.get("ask") or data.get("ask_price"))
    side = str(data.get("side") or data.get("order_side") or "").strip().lower()
    executable = ask if side == "buy" else (bid if side in {"sell", "short"} else None)
    reference = _float(
        data.get("candidate_reference_price")
        or data.get("model_reference_price")
        or data.get("reference_price")
    )
    executable_deviation = None
    if executable and reference and reference > 0:
        executable_deviation = ((executable - reference) / reference) * 10000.0
        if side in {"sell", "short"}:
            executable_deviation *= -1.0
    if spread is None and bid and ask and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
        spread = ((ask - bid) / mid) * 10000.0 if mid > 0 else None
    quote_time = _aware(data.get("quote_timestamp") or data.get("quote_time") or data.get("latest_quote_at"))
    freshness = None
    if require_fresh_quote and quote_time is None:
        return QuoteQualityResult(False, spread, freshness, "quote_timestamp_missing", executable, reference, executable_deviation)
    if quote_time is not None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        freshness = max(0.0, (current.astimezone(timezone.utc) - quote_time).total_seconds())
        if freshness > max_freshness_seconds:
            return QuoteQualityResult(False, spread, freshness, "quote_stale", executable, reference, executable_deviation)
    if max_executable_deviation_bps is not None and executable_deviation is not None and executable_deviation > max_executable_deviation_bps:
        return QuoteQualityResult(False, spread, freshness, "quote_reference_price_dislocated", executable, reference, executable_deviation)
    spread_edge = evaluate_spread_edge(
        spread_bps=spread,
        max_spread_bps=max_spread_bps,
        expected_move_bps=expected_move_bps_from(data),
        estimated_cost_bps=estimated_cost_bps,
        min_edge_to_spread_ratio=min_edge_to_spread_ratio,
        min_expected_net_edge_bps=min_expected_net_edge_bps,
    )
    if spread is not None and spread > max_spread_bps and not spread_edge.allowed:
        return QuoteQualityResult(
            False,
            spread,
            freshness,
            "spread_too_wide",
            executable,
            reference,
            executable_deviation,
            spread_edge.expected_move_bps,
            spread_edge.estimated_cost_bps,
            spread_edge.expected_net_edge_bps,
            spread_edge.edge_to_spread_ratio,
            spread_edge.decision,
        )
    return QuoteQualityResult(
        True,
        spread,
        freshness,
        "",
        executable,
        reference,
        executable_deviation,
        spread_edge.expected_move_bps,
        spread_edge.estimated_cost_bps,
        spread_edge.expected_net_edge_bps,
        spread_edge.edge_to_spread_ratio,
        spread_edge.decision,
    )
