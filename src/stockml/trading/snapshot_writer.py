from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from stockml.trading.snapshot_schema import (
    RAW_JSON_RESERVED_KEYS,
    Direction,
    FunnelStage,
    Pool,
    SNAPSHOT_COLUMNS,
    ScoreBasis,
    ScoreState,
    SnapshotRow,
    default_stage_verdicts,
    snapshot_row_to_record,
    validate_snapshot_row,
)


def first_value(row: dict[str, Any], keys: Iterable[str], default: Any = "") -> Any:
    for key in keys:
        value = row.get(key)
        if value not in [None, ""]:
            return value
    return default


def parse_generated_at(value: Any, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    match = re.search(r"(20\d{6})_(\d{6})", text)
    if match:
        try:
            return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return fallback
    return fallback


def direction_from_row(row: dict[str, Any]) -> Direction:
    value = str(first_value(row, ["direction", "side", "trade_action", "nightly_bias"], "")).strip().lower()
    if value in {"buy", "long", "long_bias"}:
        return Direction.LONG
    if value in {"sell", "short", "short_bias"}:
        return Direction.SHORT
    return Direction.NEUTRAL


def float_or_none(value: Any) -> float | None:
    if value in [None, ""]:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    number = float_or_none(value)
    if number is None:
        return None
    return int(number)


def pool_from_value(value: str) -> Pool:
    try:
        return Pool(str(value))
    except ValueError:
        raise ValueError(f"unsupported_snapshot_pool:{value}") from None


def _score_basis(pool: Pool, row: dict[str, Any]) -> ScoreBasis:
    if pool == Pool.MODEL_SHORTLIST:
        return ScoreBasis.RAW_RANK
    if pool == Pool.INTRADAY_PROMOTION:
        return ScoreBasis.PROMOTION
    if pool == Pool.PER_SYMBOL_FORECAST:
        return ScoreBasis.NONE
    if pool == Pool.ACTION_QUEUE:
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        source = str(details.get("candidate_source") or "").lower()
        if source == "per_symbol_forecast":
            return ScoreBasis.VOLATILITY_ADJUSTED
        if source == "near_miss":
            return ScoreBasis.RISK_ADJUSTED
    if pool in {Pool.NEAR_MISS, Pool.TODAYS_BASKET, Pool.REJECTED_TRIMMED}:
        return ScoreBasis.RISK_ADJUSTED
    return ScoreBasis.NONE


def _raw_score(pool: Pool, row: dict[str, Any]) -> float | None:
    if pool == Pool.PER_SYMBOL_FORECAST:
        return float_or_none(first_value(row, ["model_score", "raw_score", "volatility_adjusted_score", "risk_adjusted_score"]))
    return float_or_none(first_value(row, ["raw_score", "model_score", "nightly_score", "risk_adjusted_score", "score", "confidence_score"]))


def _display_score(pool: Pool, row: dict[str, Any]) -> float | None:
    if pool == Pool.PER_SYMBOL_FORECAST:
        return None
    if pool == Pool.INTRADAY_PROMOTION:
        return float_or_none(first_value(row, ["promotion_score", "display_score", "score"]))
    if pool == Pool.NEAR_MISS:
        return float_or_none(first_value(row, ["risk_adjusted_score", "actual_value", "display_score"]))
    return float_or_none(first_value(row, ["display_score", "promotion_score", "risk_adjusted_score", "score", "nightly_score", "confidence_score", "unrealized_plpc"]))


def _score_state(pool: Pool, raw_score: float | None, display_score: float | None) -> ScoreState:
    if display_score is not None:
        return ScoreState.AVAILABLE
    if pool == Pool.PER_SYMBOL_FORECAST:
        return ScoreState.SUPPRESSED_DIAGNOSTIC if raw_score is not None else ScoreState.MISSING_SOURCE
    if pool == Pool.OPEN_POSITIONS:
        return ScoreState.NOT_APPLICABLE
    if pool == Pool.ACTION_QUEUE:
        return ScoreState.NOT_APPLICABLE if raw_score is None else ScoreState.MISSING_SOURCE
    if pool in {Pool.MODEL_SHORTLIST, Pool.INTRADAY_PROMOTION, Pool.NEAR_MISS, Pool.TODAYS_BASKET, Pool.REJECTED_TRIMMED}:
        return ScoreState.MISSING_SOURCE
    return ScoreState.NOT_APPLICABLE


def _outcome_and_stage(pool: Pool, row: dict[str, Any]) -> tuple[str | None, FunnelStage]:
    status = str(first_value(row, ["outcome", "status", "basket_status", "trade_quality_status", "alpaca_status"], "")).strip().lower()
    verdict = str(first_value(row, ["verdict", "decision"], "")).strip().lower()
    reason = str(first_value(row, ["reason", "reason_note", "decision_reason", "trade_quality_reason", "block_reason", "message", "operator_call_reason"], "")).strip().lower()
    action = str(first_value(row, ["action", "decision", "recommended_action", "operator_call", "trade_action"], "")).strip().lower()

    if pool == Pool.NEAR_MISS:
        return "near_miss", FunnelStage.NEAR_MISS
    if pool == Pool.PER_SYMBOL_FORECAST:
        return None, FunnelStage.SCORED
    if pool == Pool.OPEN_POSITIONS:
        return "accepted", FunnelStage.FILLED
    if pool == Pool.ACTION_QUEUE:
        if "close" in action:
            return "pending", FunnelStage.SUBMITTED
        return "open_candidate", FunnelStage.SELECTED
    if pool == Pool.INTRADAY_PROMOTION:
        if verdict == "block" or status == "blocked" or reason:
            return "blocked", FunnelStage.QUALITY_GATED
        if verdict in {"watch", "allow", "promote"}:
            return "accepted", FunnelStage.QUALITY_GATED
        return None, FunnelStage.QUALITY_GATED
    if any(token in status for token in ["reject", "trim", "fail"]) or "below" in reason:
        return "rejected", FunnelStage.REJECTED
    if status in {"new", "pending", "submitted", "accepted"}:
        return "pending", FunnelStage.SUBMITTED
    if status == "filled":
        return "accepted", FunnelStage.FILLED
    if pool == Pool.TODAYS_BASKET:
        eligible = str(row.get("order_eligible", "")).strip().lower()
        if eligible in {"true", "1", "yes"} or status in {"approved", "selected"}:
            return "accepted", FunnelStage.SELECTED
        if reason:
            return "rejected", FunnelStage.REJECTED
    if pool == Pool.REJECTED_TRIMMED:
        return "rejected", FunnelStage.REJECTED
    return None, FunnelStage.SCORED


def _stage_verdicts(pool: Pool, row: dict[str, Any], *, outcome: str | None, reason: str | None) -> dict[str, Any]:
    if pool == Pool.INTRADAY_PROMOTION:
        verdict = str(first_value(row, ["verdict", "decision"], "") or "not_evaluated").lower()
        block_reason = first_value(row, ["block_reason", "reason"], "")
        value = f"block:{block_reason}" if verdict == "block" and block_reason else verdict
        return default_stage_verdicts(intraday_gate=value)
    if pool == Pool.ACTION_QUEUE:
        decision = str(first_value(row, ["decision", "recommended_action", "operator_call"], "") or "pending").lower()
        return default_stage_verdicts(monitor=decision, operator="pending")
    if outcome == "rejected":
        return default_stage_verdicts(trade_quality=f"rejected:{reason}" if reason else "rejected")
    if outcome == "accepted":
        return default_stage_verdicts(trade_quality="approved")
    return default_stage_verdicts()


def _raw_json(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    raw = {key: value for key, value in row.items() if key not in RAW_JSON_RESERVED_KEYS}
    if source:
        raw["source_artifact"] = source
    return raw


def build_snapshot_row(pool: str | Pool, row: dict[str, Any], *, snapshot_at: datetime, generated_at: Any = "", source: str = "") -> SnapshotRow:
    pool_enum = pool if isinstance(pool, Pool) else pool_from_value(pool)
    generated = parse_generated_at(generated_at or first_value(row, ["generated_at", "snapshot_at", "time", "updated_at", "logged_at"]), fallback=snapshot_at)
    reason = str(first_value(row, ["outcome_reason", "reason", "reason_note", "decision_reason", "trade_quality_reason", "block_reason", "message", "operator_call_reason"], "") or "")
    outcome, stage = _outcome_and_stage(pool_enum, row)
    raw_score = _raw_score(pool_enum, row)
    display_score = _display_score(pool_enum, row)
    snapshot_row = SnapshotRow(
        snapshot_at=snapshot_at,
        pool=pool_enum,
        symbol=str(first_value(row, ["symbol", "ticker", "replace_symbol", "with_symbol"], "") or "").upper(),
        generated_at=generated,
        direction=direction_from_row(row),
        funnel_stage=stage,
        rank=int_or_none(first_value(row, ["candidate_rank", "rank", "replacement_rank"])),
        raw_score=raw_score,
        display_score=display_score,
        score_basis=_score_basis(pool_enum, row),
        score_state=_score_state(pool_enum, raw_score, display_score),
        outcome=outcome,
        outcome_reason=reason if outcome else None,
        stage_verdicts=_stage_verdicts(pool_enum, row, outcome=outcome, reason=reason),
        notional=float_or_none(first_value(row, ["planned_notional", "approved_notional", "notional", "market_value"])),
        quantity=int_or_none(first_value(row, ["planned_quantity", "suggested_quantity", "qty", "filled_qty"])),
        data_age_seconds=max(0, int((snapshot_at - generated).total_seconds())),
        raw_json=_raw_json(row, source=source),
    )
    return validate_snapshot_row(snapshot_row)


def write_snapshot_csv(pools: Iterable[tuple[str, Iterable[dict[str, Any]], Any, str]], *, snapshot_at: datetime | None = None) -> str:
    snapshot_time = snapshot_at or datetime.now(timezone.utc)
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
    records: list[dict[str, Any]] = []
    for pool, rows, generated_at, source in pools:
        for row in rows or []:
            snapshot_row = build_snapshot_row(pool, dict(row), snapshot_at=snapshot_time, generated_at=generated_at, source=source)
            records.append(snapshot_row_to_record(snapshot_row, source=source))

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=SNAPSHOT_COLUMNS)
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue()


def write_snapshot_file(path: Path, pools: Iterable[tuple[str, Iterable[dict[str, Any]], Any, str]], *, snapshot_at: datetime | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(write_snapshot_csv(pools, snapshot_at=snapshot_at), encoding="utf-8")
    return path
