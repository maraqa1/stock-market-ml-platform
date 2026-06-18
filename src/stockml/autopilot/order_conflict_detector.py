from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from stockml.trading.anti_churn_guard import AntiChurnConfig, guard_actions


def detect_order_conflicts(actions: Iterable[dict[str, Any]], *, now: datetime | None = None, cycle_id: str | None = None, config: AntiChurnConfig | None = None) -> pd.DataFrame:
    """Return same-cycle anti-churn conflicts without submitting anything."""
    _, report = guard_actions(actions, now=now, cycle_id=cycle_id, config=config or AntiChurnConfig())
    return report


def block_conflicting_actions(actions: Iterable[dict[str, Any]], *, now: datetime | None = None, cycle_id: str | None = None, config: AntiChurnConfig | None = None) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    return guard_actions(actions, now=now, cycle_id=cycle_id, config=config or AntiChurnConfig())
