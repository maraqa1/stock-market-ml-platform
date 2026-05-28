from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from stockml.common.paths import PROJECT_ROOT, latest_file, timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import autopilot_open_log, intraday_candidate_snapshots, intraday_promotion_log


TRACE_DIR_NAME = "mover_trace"

TRACE_COLUMNS = [
    "symbol",
    "trace_reason",
    "model_trade_action",
    "model_risk_adjusted_score",
    "model_expected_trade_return",
    "candidate_status",
    "candidate_reason",
    "plan_status",
    "plan_reason",
    "forecast_confirmation",
    "forecast_reason",
    "forecast_profitability_ok",
    "forecast_side_alignment",
    "holding_quality",
    "holding_gate_pass",
    "holding_gate_reason",
    "intraday_snapshot_status",
    "intraday_snapshot_at",
    "intraday_last_price",
    "intraday_nightly_bias",
    "intraday_dollar_volume_today",
    "promotion_verdict",
    "promotion_block_reason",
    "promotion_score",
    "autopilot_verdict",
    "autopilot_block_reason",
    "autopilot_order_id",
]


@dataclass(frozen=True)
class TraceArtifacts:
    model_predictions: Path | None
    candidate_pool: Path | None
    order_plan: Path | None
    per_symbol_forecast: Path | None
    holding_review: Path | None


def parse_symbols(raw: str | Iterable[str]) -> list[str]:
    values = raw.split(",") if isinstance(raw, str) else list(raw)
    symbols = []
    seen = set()
    for value in values:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def symbols_from_movers_file(path: Path) -> list[str]:
    frame = pd.read_csv(path)
    column = _symbol_column(frame)
    if not column:
        raise ValueError(f"movers file has no symbol/ticker column: {path}")
    return parse_symbols(frame[column].tolist())


def trace_intraday_movers(
    symbols: Iterable[str],
    *,
    root: Path | None = None,
    engine: Engine | None = None,
    write: bool = True,
    stamp: str | None = None,
) -> tuple[pd.DataFrame, Path | None]:
    base = root or PROJECT_ROOT
    wanted = parse_symbols(symbols)
    artifacts = _artifacts(base)

    model_rows = _csv_rows_by_symbol(artifacts.model_predictions, wanted)
    candidate_rows = _csv_rows_by_symbol(artifacts.candidate_pool, wanted)
    plan_rows = _csv_rows_by_symbol(artifacts.order_plan, wanted)
    forecast_rows = _csv_rows_by_symbol(artifacts.per_symbol_forecast, wanted)
    review_rows = _csv_rows_by_symbol(artifacts.holding_review, wanted)
    db_rows = _db_rows(wanted, engine=engine, use_default_db=(base.resolve() == PROJECT_ROOT.resolve()))

    records = []
    for symbol in wanted:
        record = {"symbol": symbol}
        model = model_rows.get(symbol, {})
        candidate = candidate_rows.get(symbol, {})
        plan = plan_rows.get(symbol, {})
        forecast = forecast_rows.get(symbol, {})
        review = review_rows.get(symbol, {})
        db = db_rows.get(symbol, {})

        record.update(
            {
                "model_trade_action": _value(model, "trade_action"),
                "model_risk_adjusted_score": _value(model, "risk_adjusted_score"),
                "model_expected_trade_return": _value(model, "expected_trade_return"),
                "candidate_status": _value(candidate, "trade_quality_status"),
                "candidate_reason": _value(candidate, "trade_quality_reason"),
                "plan_status": _value(plan, "trade_quality_status"),
                "plan_reason": _value(plan, "trade_quality_reason"),
                "forecast_confirmation": _value(forecast, "forecast_confirmation"),
                "forecast_reason": _value(forecast, "confirmation_reason"),
                "forecast_profitability_ok": _value(forecast, "profitability_ok"),
                "forecast_side_alignment": _value(forecast, "side_alignment"),
                "holding_quality": _value(review, "holding_quality"),
                "holding_gate_pass": _value(review, "holding_gate_pass"),
                "holding_gate_reason": _value(review, "holding_gate_reason"),
                "intraday_snapshot_status": _value(db, "intraday_snapshot_status"),
                "intraday_snapshot_at": _value(db, "intraday_snapshot_at"),
                "intraday_last_price": _value(db, "intraday_last_price"),
                "intraday_nightly_bias": _value(db, "intraday_nightly_bias"),
                "intraday_dollar_volume_today": _value(db, "intraday_dollar_volume_today"),
                "promotion_verdict": _value(db, "promotion_verdict"),
                "promotion_block_reason": _value(db, "promotion_block_reason"),
                "promotion_score": _value(db, "promotion_score"),
                "autopilot_verdict": _value(db, "autopilot_verdict"),
                "autopilot_block_reason": _value(db, "autopilot_block_reason"),
                "autopilot_order_id": _value(db, "autopilot_order_id"),
            }
        )
        record["trace_reason"] = _trace_reason(model, candidate, plan, forecast, review, db)
        records.append(record)

    frame = pd.DataFrame(records, columns=TRACE_COLUMNS)
    output_path = None
    if write:
        output_dir = base / "data" / "trading" / TRACE_DIR_NAME
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"mover_trace_{stamp or timestamp()}.csv"
        frame.to_csv(output_path, index=False)
    return frame, output_path


def _artifacts(root: Path) -> TraceArtifacts:
    data = root / "data"
    return TraceArtifacts(
        model_predictions=data / "model_outputs" / "model_predictions_latest.csv",
        candidate_pool=latest_file(data / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"),
        order_plan=latest_file(data / "portal_outputs", "08_alpaca_paper_order_plan_*.csv"),
        per_symbol_forecast=latest_file(data / "trading" / "per_symbol_forecast", "per_symbol_forecast_*.csv"),
        holding_review=latest_file(data / "trading" / "holding_period", "holding_review_*.csv"),
    )


def _csv_rows_by_symbol(path: Path | None, symbols: list[str]) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return {}
    column = _symbol_column(frame)
    if not column:
        return {}
    working = frame.copy()
    working["__symbol"] = working[column].fillna("").astype(str).str.upper().str.strip()
    working = working[working["__symbol"].isin(symbols)]
    if working.empty:
        return {}
    return {str(row["__symbol"]): {k: v for k, v in row.items() if k != "__symbol"} for row in working.fillna("").to_dict("records")}


def _symbol_column(frame: pd.DataFrame) -> str | None:
    for column in ("symbol", "ticker", "yahoo_ticker"):
        if column in frame.columns:
            return column
    return None


def _db_rows(symbols: list[str], *, engine: Engine | None, use_default_db: bool) -> dict[str, dict]:
    db = engine
    if db is None and use_default_db:
        try:
            db = get_engine(required=False)
        except Exception:
            db = None
    if db is None:
        return {}

    rows = {symbol: {} for symbol in symbols}
    try:
        with db.begin() as conn:
            snapshot_rows = conn.execute(
                select(intraday_candidate_snapshots)
                .where(intraday_candidate_snapshots.c.symbol.in_(symbols))
                .order_by(intraday_candidate_snapshots.c.symbol.asc(), intraday_candidate_snapshots.c.snapshot_at.desc(), intraday_candidate_snapshots.c.id.desc())
            ).mappings()
            for row in snapshot_rows:
                symbol = str(row["symbol"]).upper()
                if symbol not in rows:
                    continue
                if rows[symbol].get("intraday_snapshot_at"):
                    continue
                rows[symbol].update(
                    {
                        "intraday_snapshot_status": row.get("status"),
                        "intraday_snapshot_at": row.get("snapshot_at"),
                        "intraday_last_price": row.get("last_price"),
                        "intraday_nightly_bias": row.get("nightly_bias"),
                        "intraday_dollar_volume_today": row.get("dollar_volume_today"),
                    }
                )

            promotion_rows = conn.execute(
                select(intraday_promotion_log)
                .where(intraday_promotion_log.c.symbol.in_(symbols))
                .order_by(intraday_promotion_log.c.symbol.asc(), intraday_promotion_log.c.logged_at.desc(), intraday_promotion_log.c.id.desc())
            ).mappings()
            for row in promotion_rows:
                symbol = str(row["symbol"]).upper()
                if symbol not in rows:
                    continue
                if rows[symbol].get("promotion_verdict"):
                    continue
                rows[symbol].update(
                    {
                        "promotion_verdict": row.get("verdict"),
                        "promotion_block_reason": row.get("block_reason"),
                        "promotion_score": row.get("promotion_score"),
                    }
                )

            autopilot_rows = conn.execute(
                select(autopilot_open_log)
                .where(autopilot_open_log.c.symbol.in_(symbols))
                .order_by(autopilot_open_log.c.symbol.asc(), autopilot_open_log.c.logged_at.desc(), autopilot_open_log.c.id.desc())
            ).mappings()
            for row in autopilot_rows:
                symbol = str(row["symbol"]).upper()
                if symbol not in rows:
                    continue
                if rows[symbol].get("autopilot_verdict"):
                    continue
                rows[symbol].update(
                    {
                        "autopilot_verdict": row.get("verdict"),
                        "autopilot_block_reason": row.get("block_reason"),
                        "autopilot_order_id": row.get("order_id"),
                    }
                )
    except Exception:
        return rows
    return rows


def _trace_reason(model: dict, candidate: dict, plan: dict, forecast: dict, review: dict, db: dict) -> str:
    autopilot_verdict = _text(db.get("autopilot_verdict"))
    if autopilot_verdict == "opened":
        return "traded_by_autopilot"
    if autopilot_verdict:
        reason = _text(db.get("autopilot_block_reason")) or "no_order_submitted"
        return f"autopilot_{autopilot_verdict}:{reason}"

    promotion_verdict = _text(db.get("promotion_verdict"))
    if promotion_verdict and promotion_verdict not in {"promote_to_selection", "promote_to_selection_strong"}:
        reason = _text(db.get("promotion_block_reason")) or "not_promoted"
        return f"intraday_{promotion_verdict}:{reason}"

    if forecast:
        confirmation = _text(forecast.get("forecast_confirmation"))
        if confirmation and confirmation != "confirmed":
            reason = _text(forecast.get("confirmation_reason")) or confirmation
            return f"forecast_not_confirmed:{reason}"

    if review and _text(review.get("holding_gate_pass")).lower() in {"false", "0", "no"}:
        reason = _text(review.get("holding_gate_reason")) or "holding_gate_failed"
        return f"holding_review_blocked:{reason}"

    if candidate:
        status = _text(candidate.get("trade_quality_status"))
        if status == "rejected":
            reason = _text(candidate.get("trade_quality_reason")) or "candidate_rejected"
            return f"candidate_rejected:{reason}"
        if not plan:
            return "candidate_not_selected_into_order_plan"
    elif model and _text(model.get("trade_action")).lower() in {"no decision", "neutral", "none", ""}:
        return "model_no_decision_not_candidate"
    elif model:
        return "not_in_candidate_pool"
    else:
        return "not_in_model_universe_or_latest_signal"

    if plan:
        status = _text(plan.get("trade_quality_status"))
        if status == "rejected":
            reason = _text(plan.get("trade_quality_reason")) or "plan_rejected"
            return f"plan_rejected:{reason}"
        if status == "approved":
            return "plan_approved_but_no_autopilot_log"

    if promotion_verdict in {"promote_to_selection", "promote_to_selection_strong"}:
        return "promoted_but_no_autopilot_log"
    return "not_seen_by_intraday_or_autopilot"


def _value(row: dict, key: str):
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, (list, dict)) and pd.isna(value):
        return ""
    return value


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()
