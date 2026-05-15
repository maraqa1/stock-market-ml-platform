from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from stockml.trading.outcome_reasons import OutcomeReason, human_label


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class FunnelStage(str, Enum):
    SCORED = "scored"
    QUALITY_GATED = "quality_gated"
    META_FILTERED = "meta_filtered"
    SIZED = "sized"
    SELECTED = "selected"
    SUBMITTED = "submitted"
    FILLED = "filled"
    REJECTED = "rejected"
    NEAR_MISS = "near_miss"


class ScoreBasis(str, Enum):
    RAW_RANK = "raw_rank"
    RISK_ADJUSTED = "risk_adjusted"
    PROMOTION = "promotion"
    VOLATILITY_ADJUSTED = "volatility_adjusted"
    META_LABEL_PROBABILITY = "meta_label_probability"
    NONE = "none"


class ScoreState(str, Enum):
    AVAILABLE = "available"
    NOT_APPLICABLE = "not_applicable"
    MISSING_SOURCE = "missing_source"
    SUPPRESSED_DIAGNOSTIC = "suppressed_diagnostic"


class Pool(str, Enum):
    MODEL_SHORTLIST = "model_shortlist"
    PER_SYMBOL_FORECAST = "per_symbol_forecast"
    NEAR_MISS = "near_miss"
    INTRADAY_PROMOTION = "intraday_promotion"
    TODAYS_BASKET = "todays_basket"
    REJECTED_TRIMMED = "rejected_trimmed"
    ACTION_QUEUE = "action_queue"
    OPEN_POSITIONS = "open_positions"


@dataclass
class SnapshotRow:
    snapshot_at: datetime
    pool: Pool
    symbol: str
    generated_at: datetime
    direction: Direction
    funnel_stage: FunnelStage
    rank: int | None
    raw_score: float | None
    display_score: float | None
    score_basis: ScoreBasis
    score_state: ScoreState
    outcome: str | None
    outcome_reason: str | None
    final_verdict: str = "not_evaluated"
    final_action: str = "review_required"
    primary_reject_reason: str | None = None
    stage_verdicts: dict[str, Any] = field(default_factory=dict)
    gate_failures: list[str] = field(default_factory=list)
    promotion_reason: str | None = None
    operator_reason: str | None = None
    raw_reason_text: str | None = None
    notional: float | None = None
    quantity: int | None = None
    data_age_seconds: int = 0
    raw_json: dict[str, Any] = field(default_factory=dict)


CANONICAL_COLUMNS = [
    "snapshot_at",
    "pool",
    "symbol",
    "generated_at",
    "direction",
    "funnel_stage",
    "rank",
    "raw_score",
    "display_score",
    "score_basis",
    "score_state",
    "outcome",
    "outcome_reason",
    "final_verdict",
    "final_action",
    "primary_reject_reason",
    "stage_verdicts",
    "gate_failures",
    "promotion_reason",
    "operator_reason",
    "raw_reason_text",
    "notional",
    "quantity",
    "data_age_seconds",
    "raw_json",
]

# Deprecated compatibility columns. Keep for one release while downstream
# notebooks and portal links migrate to the canonical fields.
DEPRECATED_SHIM_COLUMNS = ["side", "status", "action", "verdict", "score", "reason", "source"]
SNAPSHOT_COLUMNS = CANONICAL_COLUMNS + DEPRECATED_SHIM_COLUMNS

STAGE_VERDICT_KEYS = ["trade_quality", "meta_label", "sizing", "intraday_gate", "monitor", "operator"]
RAW_JSON_RESERVED_KEYS = set(CANONICAL_COLUMNS) - {"raw_json"}
FINAL_VERDICTS = {"approved", "rejected", "not_evaluated", "warning", "close_candidate", "review_required"}


def default_stage_verdicts(**updates: Any) -> dict[str, Any]:
    verdicts = {key: "not_evaluated" for key in STAGE_VERDICT_KEYS}
    verdicts.update({key: value for key, value in updates.items() if key in verdicts and value not in [None, ""]})
    return verdicts


def validate_snapshot_row(row: SnapshotRow) -> SnapshotRow:
    if not isinstance(row.pool, Pool):
        raise ValueError("invalid_snapshot_pool")
    if not isinstance(row.direction, Direction):
        raise ValueError("invalid_snapshot_direction")
    if not isinstance(row.funnel_stage, FunnelStage):
        raise ValueError("invalid_snapshot_funnel_stage")
    if not isinstance(row.score_basis, ScoreBasis):
        raise ValueError("invalid_snapshot_score_basis")
    if not isinstance(row.score_state, ScoreState):
        raise ValueError("invalid_snapshot_score_state")
    if row.outcome_reason and not row.outcome:
        raise ValueError("snapshot_reason_without_outcome")
    if row.outcome_reason:
        try:
            reason = OutcomeReason(str(row.outcome_reason))
        except ValueError as exc:
            raise ValueError("invalid_snapshot_outcome_reason") from exc
        if row.outcome == "rejected" and not reason.value.startswith("rejected_"):
            raise ValueError("rejected_reason_must_be_rejected")
        if row.outcome == "near_miss" and not reason.value.startswith("near_miss_"):
            raise ValueError("near_miss_reason_must_be_near_miss")
        if row.outcome == "blocked" and not reason.value.startswith("blocked_"):
            raise ValueError("blocked_reason_must_be_blocked")
        if row.outcome == "accepted" and reason != OutcomeReason.ACCEPTED:
            raise ValueError("accepted_reason_must_be_accepted")
        if row.outcome == "open_candidate" and reason != OutcomeReason.OPEN_CANDIDATE:
            raise ValueError("open_candidate_reason_must_be_open_candidate")
    if row.rank is not None and row.rank < 1:
        raise ValueError("snapshot_rank_must_be_positive")
    if row.data_age_seconds < 0:
        raise ValueError("snapshot_data_age_must_be_non_negative")
    if row.pool == Pool.PER_SYMBOL_FORECAST and row.display_score is not None:
        raise ValueError("per_symbol_forecast_display_score_must_be_empty")
    if row.final_verdict not in FINAL_VERDICTS:
        raise ValueError("invalid_snapshot_final_verdict")
    if row.final_verdict == "approved" and row.primary_reject_reason:
        raise ValueError("approved_row_cannot_have_primary_reject_reason")
    if row.primary_reject_reason and row.primary_reject_reason.lower() == "approved":
        raise ValueError("approved_is_not_a_reject_reason")
    if any(key in RAW_JSON_RESERVED_KEYS for key in row.raw_json):
        raise ValueError("snapshot_raw_json_duplicates_canonical")
    return row


def shim_side(direction: Direction) -> str:
    if direction == Direction.LONG:
        return "buy"
    if direction == Direction.SHORT:
        return "sell"
    return "neutral"


def shim_action(row: SnapshotRow) -> str:
    if row.pool == Pool.ACTION_QUEUE:
        return row.outcome or ""
    return row.direction.value.title()


def shim_verdict(row: SnapshotRow) -> str:
    if row.pool == Pool.INTRADAY_PROMOTION:
        return str(row.stage_verdicts.get("intraday_gate") or "")
    if row.pool == Pool.ACTION_QUEUE:
        return str(row.stage_verdicts.get("monitor") or "")
    return ""


def snapshot_row_to_record(row: SnapshotRow, *, source: str = "") -> dict[str, Any]:
    validate_snapshot_row(row)
    record = {
        "snapshot_at": row.snapshot_at.isoformat(timespec="seconds"),
        "pool": row.pool.value,
        "symbol": row.symbol,
        "generated_at": row.generated_at.isoformat(timespec="seconds"),
        "direction": row.direction.value,
        "funnel_stage": row.funnel_stage.value,
        "rank": row.rank if row.rank is not None else "",
        "raw_score": row.raw_score if row.raw_score is not None else "",
        "display_score": row.display_score if row.display_score is not None else "",
        "score_basis": row.score_basis.value,
        "score_state": row.score_state.value,
        "outcome": row.outcome or "",
        "outcome_reason": row.outcome_reason or "",
        "final_verdict": row.final_verdict,
        "final_action": row.final_action,
        "primary_reject_reason": row.primary_reject_reason or "",
        "stage_verdicts": json.dumps(row.stage_verdicts, default=str, sort_keys=True),
        "gate_failures": json.dumps(row.gate_failures, default=str),
        "promotion_reason": row.promotion_reason or "",
        "operator_reason": row.operator_reason or "",
        "raw_reason_text": row.raw_reason_text or "",
        "notional": row.notional if row.notional is not None else "",
        "quantity": row.quantity if row.quantity is not None else "",
        "data_age_seconds": row.data_age_seconds,
        "raw_json": json.dumps(row.raw_json, default=str, sort_keys=True),
    }
    record.update(
        {
            "side": shim_side(row.direction),
            "status": row.outcome or "",
            "action": shim_action(row),
            "verdict": shim_verdict(row),
            "score": row.display_score if row.display_score is not None else "",
            "reason": human_label(row.outcome_reason),
            "source": source,
        }
    )
    return record
