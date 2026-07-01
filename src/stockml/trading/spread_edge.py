from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class SpreadEdgeDecision:
    spread_bps: float | None
    max_spread_bps: float
    expected_move_bps: float | None
    estimated_cost_bps: float
    expected_net_edge_bps: float | None
    edge_to_spread_ratio: float | None
    decision: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision in {"within_limit", "wide_spread_edge_supported"}

    def details(self) -> dict[str, float | str | None]:
        return {
            "spread_bps": self.spread_bps,
            "max_spread_bps": self.max_spread_bps,
            "expected_move_bps": self.expected_move_bps,
            "estimated_cost_bps": self.estimated_cost_bps,
            "expected_net_edge_bps": self.expected_net_edge_bps,
            "edge_to_spread_ratio": self.edge_to_spread_ratio,
            "spread_gate_decision": self.decision,
            "spread_gate_reason": self.reason,
        }


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


def expected_move_bps_from(data: dict[str, Any] | pd.Series) -> float | None:
    get = data.get
    for key in [
        "expected_move_bps_calibrated",
        "expected_move_bps",
        "directional_expected_edge_bps",
        "expected_5d_return_bps",
        "expected_1d_return_bps",
    ]:
        value = _float(get(key))
        if value is not None:
            return abs(value)
    expected_trade_return = _float(get("expected_trade_return"))
    if expected_trade_return is not None:
        return abs(expected_trade_return) * 10000.0
    probability_edge = _float(get("probability_edge"))
    if probability_edge is not None:
        return abs(probability_edge) * 10000.0
    return None


def evaluate_spread_edge(
    *,
    spread_bps: float | None,
    max_spread_bps: float,
    expected_move_bps: float | None,
    estimated_cost_bps: float = 10.0,
    min_edge_to_spread_ratio: float = 3.0,
    min_expected_net_edge_bps: float = 25.0,
) -> SpreadEdgeDecision:
    if spread_bps is None:
        return SpreadEdgeDecision(None, max_spread_bps, expected_move_bps, estimated_cost_bps, None, None, "spread_missing")
    if spread_bps <= max_spread_bps:
        ratio = (expected_move_bps / spread_bps) if expected_move_bps is not None and spread_bps > 0 else None
        net = (expected_move_bps - spread_bps - estimated_cost_bps) if expected_move_bps is not None else None
        return SpreadEdgeDecision(spread_bps, max_spread_bps, expected_move_bps, estimated_cost_bps, net, ratio, "within_limit")
    if expected_move_bps is None:
        return SpreadEdgeDecision(spread_bps, max_spread_bps, None, estimated_cost_bps, None, None, "wide_spread_edge_missing", "expected_move_missing")
    expected_net_edge_bps = expected_move_bps - spread_bps - estimated_cost_bps
    edge_to_spread_ratio = expected_move_bps / spread_bps if spread_bps > 0 else None
    if expected_net_edge_bps < min_expected_net_edge_bps:
        return SpreadEdgeDecision(
            spread_bps,
            max_spread_bps,
            expected_move_bps,
            estimated_cost_bps,
            expected_net_edge_bps,
            edge_to_spread_ratio,
            "wide_spread_edge_insufficient",
            "expected_net_edge_below_threshold",
        )
    if edge_to_spread_ratio is None or edge_to_spread_ratio < min_edge_to_spread_ratio:
        return SpreadEdgeDecision(
            spread_bps,
            max_spread_bps,
            expected_move_bps,
            estimated_cost_bps,
            expected_net_edge_bps,
            edge_to_spread_ratio,
            "wide_spread_edge_insufficient",
            "edge_to_spread_ratio_below_threshold",
        )
    return SpreadEdgeDecision(
        spread_bps,
        max_spread_bps,
        expected_move_bps,
        estimated_cost_bps,
        expected_net_edge_bps,
        edge_to_spread_ratio,
        "wide_spread_edge_supported",
    )
