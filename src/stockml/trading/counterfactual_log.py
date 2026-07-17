from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, TRADING_DIR, latest_file, timestamp
from stockml.diagnostics.common import gold_outcome_slice, latest_gold, norm_symbol_column, normalize_outcome_columns


COUNTERFACTUAL_DIR = TRADING_DIR / "forward_paper"
COUNTERFACTUAL_COLUMNS = [
    "decision_date",
    "decision_time",
    "cycle_id",
    "pipeline_run_id",
    "symbol",
    "side",
    "trade_action",
    "raw_rank",
    "execution_rank",
    "status",
    "execution_domain",
    "order_eligible",
    "trade_quality_status",
    "primary_block_reason",
    "all_block_reasons",
    "decision_price",
    "price_source",
    "validated_expected_return_bps",
    "estimated_execution_cost_bps",
    "net_expected_return_bps",
    "expected_return_scope",
    "candidate_source_path",
    "order_plan_path",
]

FORWARD_RETURN_COLUMNS = [
    *COUNTERFACTUAL_COLUMNS,
    "forward_5d_return",
    "forward_5d_alpha_vs_spy",
    "forward_5d_alpha_vs_sector",
    "directional_forward_5d_bps",
    "directional_alpha_vs_spy_5d_bps",
    "directional_alpha_vs_sector_5d_bps",
    "outcome_status",
    "gold_outcome_path",
]


@dataclass(frozen=True)
class CounterfactualOutput:
    path: Path
    rows: int
    metadata_path: Path


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _symbol(row: pd.Series) -> str:
    return (_text(row.get("symbol")) or _text(row.get("ticker"))).upper()


def _side(row: pd.Series) -> str:
    side = _text(row.get("side")).lower()
    action = _text(row.get("trade_action")).lower()
    if side in {"buy", "long"} or action == "long":
        return "buy"
    if side in {"sell", "short"} or action == "short":
        return "sell"
    return side or action


def _decision_price(row: pd.Series) -> tuple[float | None, str]:
    for column in ["current_price", "last", "close", "adj_close", "price", "limit_price"]:
        value = _num(row.get(column))
        if value is not None and value > 0:
            return value, column
    return None, ""


def _first(row: pd.Series, columns: list[str], default: Any = "") -> Any:
    for column in columns:
        value = row.get(column)
        if _text(value):
            return value
    return default


def _merge_plan_fields(candidates: pd.DataFrame, plan: pd.DataFrame | None) -> pd.DataFrame:
    out = candidates.copy()
    if plan is None or plan.empty or "symbol" not in out.columns or "symbol" not in plan.columns:
        return out
    left = out.copy()
    right = plan.copy()
    left["__symbol"] = left["symbol"].astype(str).str.upper().str.strip()
    right["__symbol"] = right["symbol"].astype(str).str.upper().str.strip()
    keep = [
        "__symbol",
        "execution_rank",
        "execution_domain",
        "status",
        "primary_block_reason",
        "all_block_reasons",
        "order_eligible",
        "trade_quality_status",
        "trade_quality_reason",
        "estimated_execution_cost_bps",
        "net_expected_return_bps",
    ]
    available = [column for column in keep if column in right.columns]
    if "__symbol" not in available:
        return out
    right = right[available].drop_duplicates("__symbol", keep="first")
    merged = left.merge(right, on="__symbol", how="left", suffixes=("", "_plan"))
    for column in [c for c in available if c != "__symbol"]:
        plan_col = f"{column}_plan"
        if plan_col in merged.columns:
            if column in merged.columns:
                merged[column] = merged[column].where(merged[column].notna() & (merged[column].astype(str) != ""), merged[plan_col])
                merged = merged.drop(columns=[plan_col])
            else:
                merged[column] = merged[plan_col]
                merged = merged.drop(columns=[plan_col])
    return merged.drop(columns=["__symbol"])


def build_counterfactual_candidates(
    candidates: pd.DataFrame,
    *,
    plan: pd.DataFrame | None = None,
    decision_time: datetime | None = None,
    cycle_id: str = "",
    pipeline_run_id: str = "",
    candidate_source_path: str | Path = "",
    order_plan_path: str | Path = "",
) -> pd.DataFrame:
    decided_at = decision_time or datetime.now(timezone.utc)
    if decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=timezone.utc)
    merged = _merge_plan_fields(candidates if candidates is not None else pd.DataFrame(), plan)
    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        price, price_source = _decision_price(row)
        rows.append(
            {
                "decision_date": decided_at.date().isoformat(),
                "decision_time": decided_at.isoformat(),
                "cycle_id": cycle_id,
                "pipeline_run_id": pipeline_run_id,
                "symbol": _symbol(row),
                "side": _side(row),
                "trade_action": _text(row.get("trade_action")),
                "raw_rank": _first(row, ["raw_rank", "candidate_rank", "rank_overall", "research_rank"]),
                "execution_rank": row.get("execution_rank", ""),
                "status": _first(row, ["status", "candidate_status", "trade_quality_status"]),
                "execution_domain": row.get("execution_domain", ""),
                "order_eligible": row.get("order_eligible", ""),
                "trade_quality_status": row.get("trade_quality_status", ""),
                "primary_block_reason": _first(row, ["primary_block_reason", "trade_quality_reason", "message"]),
                "all_block_reasons": _first(row, ["all_block_reasons", "trade_quality_reason", "message"]),
                "decision_price": price if price is not None else "",
                "price_source": price_source,
                "validated_expected_return_bps": row.get("validated_expected_return_bps", ""),
                "estimated_execution_cost_bps": row.get("estimated_execution_cost_bps", ""),
                "net_expected_return_bps": row.get("net_expected_return_bps", ""),
                "expected_return_scope": row.get("expected_return_scope", ""),
                "candidate_source_path": str(candidate_source_path),
                "order_plan_path": str(order_plan_path),
            }
        )
    return pd.DataFrame(rows, columns=COUNTERFACTUAL_COLUMNS)


def write_counterfactual_candidates(
    candidates: pd.DataFrame,
    *,
    plan: pd.DataFrame | None = None,
    decision_time: datetime | None = None,
    cycle_id: str = "",
    pipeline_run_id: str = "",
    candidate_source_path: str | Path = "",
    order_plan_path: str | Path = "",
    output_dir: Path | None = None,
    stamp: str | None = None,
) -> CounterfactualOutput:
    run_stamp = stamp or timestamp()
    out_dir = output_dir or COUNTERFACTUAL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = build_counterfactual_candidates(
        candidates,
        plan=plan,
        decision_time=decision_time,
        cycle_id=cycle_id,
        pipeline_run_id=pipeline_run_id,
        candidate_source_path=candidate_source_path,
        order_plan_path=order_plan_path,
    )
    path = out_dir / f"counterfactual_candidates_{run_stamp}.csv"
    metadata_path = out_dir / f"counterfactual_candidates_{run_stamp}.metadata.json"
    frame.to_csv(path, index=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "path": str(path),
        "rows": int(len(frame)),
        "was_truncated": False,
        "cycle_id": cycle_id,
        "pipeline_run_id": pipeline_run_id,
        "candidate_source_path": str(candidate_source_path),
        "order_plan_path": str(order_plan_path),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return CounterfactualOutput(path=path, rows=len(frame), metadata_path=metadata_path)


def latest_counterfactual_candidates(root: Path | str | None = None) -> Path | None:
    base = Path(root) if root else PROJECT_ROOT
    return latest_file(base / "data" / "trading" / "forward_paper", "counterfactual_candidates_*.csv")


def attach_counterfactual_forward_returns(
    counterfactual: pd.DataFrame,
    *,
    gold_path: Path | None = None,
) -> pd.DataFrame:
    out = norm_symbol_column(counterfactual)
    if out.empty:
        return pd.DataFrame(columns=FORWARD_RETURN_COLUMNS)
    if "date" not in out.columns:
        out["date"] = out.get("decision_date", "")
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    gold_source = gold_path or latest_gold()
    outcomes = gold_outcome_slice(gold_source, out)
    merged = normalize_outcome_columns(out)
    if not outcomes.empty:
        merged = merged.drop(columns=[c for c in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector"] if c in merged.columns])
        merged = merged.merge(outcomes, on=["ticker", "date"], how="left")
    else:
        for column in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector"]:
            if column not in merged.columns:
                merged[column] = pd.NA
    sign = merged.get("side", pd.Series("", index=merged.index)).astype(str).str.lower().map({"buy": 1.0, "long": 1.0, "sell": -1.0, "short": -1.0}).fillna(0.0)
    for source, target in [
        ("forward_5d_return", "directional_forward_5d_bps"),
        ("forward_5d_alpha_vs_spy", "directional_alpha_vs_spy_5d_bps"),
        ("forward_5d_alpha_vs_sector", "directional_alpha_vs_sector_5d_bps"),
    ]:
        merged[target] = pd.to_numeric(merged.get(source), errors="coerce") * sign * 10000.0
    merged["outcome_status"] = "insufficient_data"
    has_outcome = pd.to_numeric(merged.get("forward_5d_return"), errors="coerce").notna()
    merged.loc[has_outcome, "outcome_status"] = "ok"
    merged["gold_outcome_path"] = str(gold_source or "")
    if "symbol" not in merged.columns and "ticker" in merged.columns:
        merged["symbol"] = merged["ticker"]
    return merged.reindex(columns=FORWARD_RETURN_COLUMNS)


def write_counterfactual_forward_returns(
    counterfactual_path: Path | str | None = None,
    *,
    gold_path: Path | str | None = None,
    output_dir: Path | None = None,
    stamp: str | None = None,
) -> CounterfactualOutput:
    source = Path(counterfactual_path) if counterfactual_path else latest_counterfactual_candidates()
    frame = pd.read_csv(source, low_memory=False) if source and source.exists() and source.stat().st_size else pd.DataFrame(columns=COUNTERFACTUAL_COLUMNS)
    gold = Path(gold_path) if gold_path else None
    out = attach_counterfactual_forward_returns(frame, gold_path=gold)
    run_stamp = stamp or timestamp()
    out_dir = output_dir or COUNTERFACTUAL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"counterfactual_forward_returns_{run_stamp}.csv"
    metadata_path = out_dir / f"counterfactual_forward_returns_{run_stamp}.metadata.json"
    out.to_csv(path, index=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "path": str(path),
        "rows": int(len(out)),
        "counterfactual_path": str(source or ""),
        "gold_outcome_path": str(gold or latest_gold() or ""),
        "ok_outcomes": int((out.get("outcome_status", pd.Series(dtype=str)) == "ok").sum()),
        "insufficient_data_outcomes": int((out.get("outcome_status", pd.Series(dtype=str)) == "insufficient_data").sum()),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return CounterfactualOutput(path=path, rows=len(out), metadata_path=metadata_path)
