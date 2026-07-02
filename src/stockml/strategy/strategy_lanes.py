from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class StrategyLane:
    lane_name: str
    executable: bool
    default_mode: str
    allowed_sides: str
    requires_source_trade_action: str
    requires_calibrated_expected_return: bool
    requires_must_have_gates: bool
    max_trades_per_day: int
    description: str


LANES: dict[str, StrategyLane] = {
    "nightly_swing_long": StrategyLane(
        lane_name="nightly_swing_long",
        executable=True,
        default_mode="validation",
        allowed_sides="Long",
        requires_source_trade_action="Long",
        requires_calibrated_expected_return=True,
        requires_must_have_gates=True,
        max_trades_per_day=1,
        description="Main validation lane: long-only nightly swing candidates.",
    ),
    "short_research": StrategyLane(
        lane_name="short_research",
        executable=False,
        default_mode="research_only",
        allowed_sides="Short",
        requires_source_trade_action="Short",
        requires_calibrated_expected_return=True,
        requires_must_have_gates=True,
        max_trades_per_day=0,
        description="Short candidates remain research-only until side-specific attribution passes.",
    ),
    "intraday_momentum_research": StrategyLane(
        lane_name="intraday_momentum_research",
        executable=False,
        default_mode="research_only",
        allowed_sides="Long,Short",
        requires_source_trade_action="directional_or_mover",
        requires_calibrated_expected_return=False,
        requires_must_have_gates=True,
        max_trades_per_day=0,
        description="Live movers and high relative-volume names for diagnostics only.",
    ),
    "raw_candidate_experiment": StrategyLane(
        lane_name="raw_candidate_experiment",
        executable=False,
        default_mode="disabled_paper_experiment",
        allowed_sides="Long",
        requires_source_trade_action="optional",
        requires_calibrated_expected_return=False,
        requires_must_have_gates=False,
        max_trades_per_day=3,
        description="Separate tiny paper-only experiment lane, disabled by default.",
    ),
    "rejected_diagnostics": StrategyLane(
        lane_name="rejected_diagnostics",
        executable=False,
        default_mode="diagnostics",
        allowed_sides="Long,Short,No Decision",
        requires_source_trade_action="none",
        requires_calibrated_expected_return=False,
        requires_must_have_gates=False,
        max_trades_per_day=0,
        description="Stores rejected candidates and full block reasons; never executable.",
    ),
}


MAIN_STRATEGY_POLICY = {
    "name": "nightly_swing_long_validation",
    "executable_lanes": ["nightly_swing_long"],
    "disabled_lanes": ["short_research", "intraday_momentum_research", "raw_candidate_experiment"],
    "max_new_orders_per_day": 1,
    "max_new_orders_per_cycle": 1,
    "max_open_positions_total": 3,
    "allow_shorts": False,
    "allow_24x5_execution": "diagnostics_only",
    "require_expected_return_calibration": True,
    "require_source_trade_action_executable": True,
    "block_new_entries_when_all_positions_red": True,
    "block_new_entries_when_open_book_drawdown_active": True,
}


def get_lane(name: str) -> StrategyLane:
    return LANES[str(name)]


def lane_frame() -> pd.DataFrame:
    return pd.DataFrame([asdict(lane) for lane in LANES.values()]).sort_values("lane_name").reset_index(drop=True)


def assign_lane(row: dict) -> str:
    status = str(row.get("status") or row.get("trade_quality_status") or "").strip().lower()
    action = str(row.get("source_trade_action") or row.get("trade_action") or "").strip().lower()
    side = str(row.get("side") or "").strip().lower()
    if status in {"rejected", "blocked"}:
        return "rejected_diagnostics"
    if action == "short" or side == "sell":
        return "short_research"
    if action == "long" or side == "buy":
        return "nightly_swing_long"
    if action in {"no decision", "no_decision", "neutral"}:
        return "rejected_diagnostics"
    return "intraday_momentum_research"
