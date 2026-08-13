from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from stockml.trading_brain_v2.shared.models import PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class AttributionRow:
    group: str
    key: str
    count: int
    total_pnl: float
    avg_pnl_pct: float


class PerformanceAttributionBlock(PlaceholderBlock):
    block_id = "PM-B11"
    name = "Performance Attribution"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        rows = self.attribute((payload or {}).get("positions") or [])
        return BrainBlockResult(block_id=self.block_id, status="ok", decision="ATTRIBUTED", reason="performance_attribution_complete", details={"rows": [row.__dict__ for row in rows]})

    def attribute(self, positions: Iterable[PositionState]) -> list[AttributionRow]:
        buckets: dict[tuple[str, str], list[PositionState]] = defaultdict(list)
        for position in positions:
            buckets[("ai2_status", position.ai2_status_at_entry or "unknown")].append(position)
            buckets[("risk_tier", position.risk_tier or "unknown")].append(position)
            buckets[("proceed_review_refresh", position.ai2_status_at_entry or "unknown")].append(position)
            buckets[("price_check_profile", "clean" if "price_checks_clear" in set(position.warnings_at_entry) else "warning")].append(position)
            buckets[("holding_period", position.max_holding_period or "unknown")].append(position)
            buckets[("volatility_bucket", "high_volatility" if "high_volatility" in set(position.warnings_at_entry) else "normal_volatility")].append(position)
            for code in position.warnings_at_entry or ("none",):
                buckets[("warning_code", code)].append(position)
        rows = []
        for (group, key), values in sorted(buckets.items()):
            rows.append(
                AttributionRow(
                    group=group,
                    key=key,
                    count=len(values),
                    total_pnl=round(sum(float(position.unrealized_pl or 0.0) for position in values), 2),
                    avg_pnl_pct=round(sum(float(position.unrealized_pl_pct or 0.0) for position in values) / len(values), 6),
                )
            )
        return rows
